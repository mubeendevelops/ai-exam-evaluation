"""P4 adapters without services: argon2id + pepper, AES-GCM, key managers, secrets, the
password list and its Bloom filter, JSON Schemas and production settings."""

import io
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from tarn_adapters.auth.crypto import (
    AesGcmCipher,
    GcpKmsKeyManager,
    LocalKeyManager,
    RoutingKeyManager,
)
from tarn_adapters.auth.hashing import Argon2Hasher
from tarn_adapters.auth.mail import ConsoleMailer, DeferredMailer, SmtpMailer
from tarn_adapters.auth.passwords import BloomFilter, CommonPasswordList, bundled_bloom_bytes
from tarn_adapters.auth.schemas import (
    IDENTITY_RECORD,
    TENANT_REGISTRY,
    JsonSchemaValidator,
    schema_text,
)
from tarn_adapters.auth.secrets import PEPPER, GcpSecrets, SettingsSecrets
from tarn_adapters.config import Settings
from tarn_core.domain.identity import HashParams
from tarn_core.errors import InvariantError
from tarn_core.ports.identity import EmailMessage

DOCS = Path(__file__).resolve().parents[3] / "docs" / "auth"
CHEAP = HashParams(time_cost=1, memory_cost=1024, parallelism=1)


# --- hashing ----------------------------------------------------------------------------------


def test_argon2id_with_pepper() -> None:
    hasher = Argon2Hasher(b"p" * 32)
    encoded = hasher.hash("correct horse battery staple", CHEAP)
    assert encoded.startswith("$argon2id$v=19$m=1024,t=1,p=1$")
    assert hasher.verify(encoded, "correct horse battery staple")
    assert not hasher.verify(encoded, "correct horse battery stapl")
    # Without the pepper the hash is useless.
    assert not Argon2Hasher(b"q" * 32).verify(encoded, "correct horse battery staple")
    assert not hasher.verify("not a hash", "x")
    with pytest.raises(ValueError, match="16 bytes"):
        Argon2Hasher(b"short")


def test_needs_rehash_follows_the_policy() -> None:
    hasher = Argon2Hasher(b"p" * 32)
    encoded = hasher.hash("pw", CHEAP)
    assert not hasher.needs_rehash(encoded, CHEAP)
    assert hasher.needs_rehash(encoded, HashParams(time_cost=2, memory_cost=1024, parallelism=1))
    assert hasher.needs_rehash(encoded, HashParams(time_cost=1, memory_cost=2048, parallelism=1))
    assert hasher.needs_rehash("garbage", CHEAP)


# --- encryption -------------------------------------------------------------------------------


def test_aes_gcm_detects_tampering_and_wrong_aad() -> None:
    cipher = AesGcmCipher()
    key = b"k" * 32
    sealed = cipher.seal(key, b"secret", b"aad")
    assert cipher.seal(key, b"secret", b"aad") != sealed  # fresh nonce
    assert cipher.open(key, sealed, b"aad") == b"secret"
    with pytest.raises(ValueError):
        cipher.open(key, sealed, b"other")
    with pytest.raises(ValueError):
        cipher.open(b"x" * 32, sealed, b"aad")
    with pytest.raises(ValueError):
        cipher.open(key, sealed[:-1] + bytes([sealed[-1] ^ 1]), b"aad")


def test_local_key_manager_creates_a_private_key_file(tmp_path: Path) -> None:
    keys = LocalKeyManager(tmp_path / "keys")
    data_key = keys.generate_data_key("local:dev", b"tenant-1")
    path = tmp_path / "keys" / "dev.key"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert keys.unwrap("local:dev", data_key.wrapped, b"tenant-1") == data_key.plaintext
    assert data_key.plaintext not in data_key.wrapped
    with pytest.raises(ValueError):
        keys.unwrap("local:dev", data_key.wrapped, b"tenant-2")  # bound to its tenant
    with pytest.raises(ValueError):
        keys.unwrap("local:other", data_key.wrapped, b"tenant-1")
    with pytest.raises(ValueError):
        keys.generate_data_key("local:../escape", b"t")


class FakeKms:
    """Records requests; 'encrypts' by reversing and prefixing the key name."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def encrypt(self, request: dict[str, object]) -> object:
        self.requests.append(request)
        plaintext = request["plaintext"]
        assert isinstance(plaintext, bytes)
        return SimpleNamespace(ciphertext=b"wrapped:" + plaintext[::-1])

    def decrypt(self, request: dict[str, object]) -> object:
        self.requests.append(request)
        if request["additional_authenticated_data"] != b"ctx":
            raise RuntimeError("PERMISSION_DENIED")
        ciphertext = request["ciphertext"]
        assert isinstance(ciphertext, bytes)
        return SimpleNamespace(plaintext=ciphertext.removeprefix(b"wrapped:")[::-1])


def test_gcp_kms_key_manager_sends_the_key_name_and_context() -> None:
    kms = FakeKms()
    keys = GcpKmsKeyManager(kms)
    ref = "gcp-kms:projects/p/locations/asia-south1/keyRings/tarn/cryptoKeys/identity"
    data_key = keys.generate_data_key(ref, b"ctx")
    assert kms.requests[0]["name"] == ref.removeprefix("gcp-kms:")
    assert kms.requests[0]["additional_authenticated_data"] == b"ctx"
    assert keys.unwrap(ref, data_key.wrapped, b"ctx") == data_key.plaintext
    with pytest.raises(ValueError, match="Cloud KMS refused"):
        keys.unwrap(ref, data_key.wrapped, b"other")
    with pytest.raises(ValueError):
        keys.generate_data_key("local:dev", b"ctx")


def test_routing_key_manager(tmp_path: Path) -> None:
    routing = RoutingKeyManager(local=LocalKeyManager(tmp_path), gcp=None)
    key = routing.generate_data_key("local:a", b"c")
    assert routing.unwrap("local:a", key.wrapped, b"c") == key.plaintext
    with pytest.raises(ValueError, match="no key manager"):
        routing.generate_data_key("gcp-kms:projects/p", b"c")


# --- secrets ----------------------------------------------------------------------------------


def test_secret_sources() -> None:
    settings = Settings(password_pepper=SecretStr("pepper-from-env-0123"))
    assert SettingsSecrets(settings).get(PEPPER) == b"pepper-from-env-0123"
    calls: list[dict[str, object]] = []

    class Client:
        def access_secret_version(self, request: dict[str, object]) -> object:
            calls.append(request)
            return SimpleNamespace(payload=SimpleNamespace(data=b"from-secret-manager"))

    gcp = GcpSecrets("my-project", Client())
    assert gcp.get(PEPPER) == b"from-secret-manager"
    gcp.get(PEPPER)  # cached
    assert calls == [{"name": "projects/my-project/secrets/tarn-password-pepper/versions/latest"}]


def test_production_refuses_development_secrets_and_keys() -> None:
    with pytest.raises(ValueError, match="unsafe production settings"):
        Settings(env="production")
    with pytest.raises(ValueError, match="cloud KMS key"):
        Settings(env="production", secrets_backend="gcp")
    production = {
        "env": "production",
        "kms_key_ref": "gcp-kms:projects/p/locations/l/keyRings/r/cryptoKeys/k",
        "secrets_backend": "gcp",
    }
    with pytest.raises(ValueError, match="BLOB_BACKEND=minio"):
        Settings(**production, mailer="smtp", smtp_host="h", smtp_from="a@b.example")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="MAILER=console"):
        Settings(**production, blob_backend="gcs")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="SMTP_HOST"):
        Settings(**production, blob_backend="gcs", mailer="smtp")  # type: ignore[arg-type]
    ok = Settings(
        **production,  # type: ignore[arg-type]
        blob_backend="gcs",
        mailer="smtp",
        smtp_host="smtp.example.test",
        smtp_from="tarn@example.test",
    )
    from tarn_adapters.auth.wiring import build_mailer

    assert isinstance(build_mailer(ok, SettingsSecrets(Settings())), SmtpMailer)
    unchecked = Settings.model_construct(env="production", mailer="console")
    with pytest.raises(RuntimeError, match="mail adapter"):
        build_mailer(unchecked, SettingsSecrets(Settings()))


# --- password list and Bloom filter ----------------------------------------------------------


def test_common_password_list() -> None:
    words = CommonPasswordList()
    assert len(words) > 90_000
    assert "password" in words and "PassWord" in words and "123456" in words
    assert "correct horse battery staple x9" not in words


def test_bloom_filter_has_no_false_negatives_and_a_low_false_positive_rate() -> None:
    words = CommonPasswordList()
    bloom = BloomFilter.from_bytes(bundled_bloom_bytes())
    assert bloom.m > 1_000_000 and bloom.k >= 7  # sized for the list, not 32 bits
    assert all(w in bloom for w in list(words)[:20_000])
    probes = [f"tarn-not-a-common-password-{i}" for i in range(20_000)]
    false_positives = sum(p in bloom for p in probes)
    assert false_positives / len(probes) < 0.003  # built for 0.1 %


def test_bloom_filter_format() -> None:
    bloom = BloomFilter.build(["alpha", "beta"], p=0.01)
    again = BloomFilter.from_bytes(bloom.to_bytes())
    assert "alpha" in again and "BETA" in again
    with pytest.raises(ValueError):
        BloomFilter.from_bytes(b"XXXX" + bloom.to_bytes()[4:])
    with pytest.raises(ValueError):
        BloomFilter.from_bytes(bloom.to_bytes()[:-1])


# --- JSON Schemas -----------------------------------------------------------------------------


def test_packaged_schemas_equal_the_published_ones() -> None:
    for name in (IDENTITY_RECORD, TENANT_REGISTRY):
        assert schema_text(name) == (DOCS / name).read_text()


def test_the_boss_examples_validate_with_neutral_names() -> None:
    validator = JsonSchemaValidator()
    identity = json.loads((DOCS / "identity-record.example.json").read_text())
    validator.validate_identity(identity)
    tenant = json.loads((DOCS / "tenant-registry.example.json").read_text())
    with pytest.raises(InvariantError):
        validator.validate_tenant(tenant)  # kms_key_arn is not a schema field
    tenant["kms_key_ref"] = tenant.pop("kms_key_arn")
    validator.validate_tenant(tenant)


def test_schema_errors_never_echo_values() -> None:
    identity = json.loads((DOCS / "identity-record.example.json").read_text())
    identity["authentication"]["password_hash"] = "plain-text-secret"
    with pytest.raises(InvariantError) as caught:
        JsonSchemaValidator().validate_identity(identity)
    assert "plain-text-secret" not in str(caught.value)
    assert "authentication/password_hash" in str(caught.value)


# --- mail -------------------------------------------------------------------------------------


def test_deferred_mailer_sends_only_on_flush() -> None:
    out = io.StringIO()
    mailer = DeferredMailer(ConsoleMailer(out))
    mailer.send(EmailMessage(to="a@b.example", subject="Hi", text="Body"))
    assert out.getvalue() == ""
    mailer.flush()
    assert "To: a@b.example" in out.getvalue() and "Body" in out.getvalue()


# --- the SMTP mailer ---------------------------------------------------------------------------


class FakeSmtp:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.sent: list[object] = []
        self._fail = fail

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("quit")

    def starttls(self, *, context: object) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login {user} {password}")

    def send_message(self, msg: object) -> None:
        if self._fail is not None:
            raise self._fail
        self.sent.append(msg)


def test_the_smtp_mailer_upgrades_to_tls_before_it_logs_in() -> None:
    smtp = FakeSmtp()
    mailer = SmtpMailer(
        host="h",
        port=587,
        sender="Tarn <tarn@example.test>",
        user="u",
        password="p",
        connect=lambda: smtp,
    )
    mailer.send(EmailMessage(to="a@b.example", subject="Verify", text="https://x/?token=t"))
    assert smtp.calls == ["starttls", "login u p", "quit"]
    (message,) = smtp.sent
    assert message["To"] == "a@b.example" and message["From"] == "Tarn <tarn@example.test>"  # type: ignore[index]
    assert "token=t" in message.get_content()  # type: ignore[attr-defined]


def test_the_smtp_mailer_on_port_465_is_tls_from_the_start() -> None:
    smtp = FakeSmtp()
    SmtpMailer(host="h", port=465, sender="t@e.test", connect=lambda: smtp).send(
        EmailMessage(to="a@b.example", subject="s", text="t")
    )
    assert smtp.calls == ["quit"]  # no STARTTLS, and no login without a user


def test_a_failed_send_is_logged_by_class_only_and_does_not_fail_the_request() -> None:
    from structlog.testing import capture_logs

    smtp = FakeSmtp(fail=ConnectionError("550 mailbox a@b.example refused; token=secret"))
    with capture_logs() as logs:
        SmtpMailer(host="h", port=587, sender="t@e.test", connect=lambda: smtp).send(
            EmailMessage(to="a@b.example", subject="s", text="token=secret")
        )
    assert [(e["event"], e["error"]) for e in logs] == [("mail.send_failed", "ConnectionError")]
    assert "secret" not in str(logs) and "a@b.example" not in str(logs)
