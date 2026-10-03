"""Build the P4 adapters from settings: key manager, hasher, auth kit."""

from datetime import timedelta
from functools import lru_cache

from tarn_adapters.auth.crypto import (
    AesGcmCipher,
    GcpKmsKeyManager,
    LocalKeyManager,
    RoutingKeyManager,
)
from tarn_adapters.auth.hashing import Argon2Hasher
from tarn_adapters.auth.mail import ConsoleMailer
from tarn_adapters.auth.passwords import CommonPasswordList
from tarn_adapters.auth.secrets import PEPPER, SecretSource, secret_source
from tarn_adapters.auth.system import SystemRandom
from tarn_adapters.config import Settings
from tarn_core.ports.identity import Mailer
from tarn_core.services.auth import AuthKit, AuthSettings


def key_manager(settings: Settings) -> RoutingKeyManager:
    """Local file keys are available only outside production; Cloud KMS when configured."""
    local = None if settings.env == "production" else LocalKeyManager(settings.local_key_dir)
    gcp = GcpKmsKeyManager() if settings.kms_key_ref.startswith(GcpKmsKeyManager.PREFIX) else None
    return RoutingKeyManager(local=local, gcp=gcp)


def auth_settings(settings: Settings) -> AuthSettings:
    return AuthSettings(
        public_url=settings.public_url,
        session=timedelta(hours=settings.session_hours),
        remembered_session=timedelta(days=settings.remember_session_days),
        signup_requires_approval=settings.tenant_signup_requires_approval,
        default_kms_key_ref=settings.kms_key_ref,
    )


@lru_cache(maxsize=1)
def common_passwords() -> CommonPasswordList:
    return CommonPasswordList()


def auth_kit(
    settings: Settings, *, secrets: SecretSource | None = None, mailer: Mailer | None = None
) -> AuthKit:
    secrets = secrets or secret_source(settings)
    if mailer is None and settings.env == "production":
        # The console mailer prints reset links, which are secrets (P20 adds a real one).
        raise RuntimeError("no production mail adapter is configured")
    return AuthKit(
        hasher=Argon2Hasher(secrets.get(PEPPER)),
        keys=key_manager(settings),
        cipher=AesGcmCipher(),
        random=SystemRandom(),
        mailer=mailer or ConsoleMailer(),
        common_passwords=common_passwords(),
        settings=auth_settings(settings),
    )
