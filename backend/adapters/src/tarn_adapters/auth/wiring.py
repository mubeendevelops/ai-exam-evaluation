"""Build the P4 adapters from settings: key manager, hasher, auth kit."""

from datetime import timedelta
from functools import lru_cache

from tarn_adapters.auth.crypto import (
    AesGcmCipher,
    AwsKmsKeyManager,
    GcpKmsKeyManager,
    LocalKeyManager,
    RoutingKeyManager,
)
from tarn_adapters.auth.hashing import Argon2Hasher
from tarn_adapters.auth.mail import ConsoleMailer, SmtpMailer
from tarn_adapters.auth.passwords import CommonPasswordList
from tarn_adapters.auth.secrets import (
    PEPPER,
    SMTP_PASSWORD,
    SecretSource,
    previous_peppers,
    secret_source,
)
from tarn_adapters.auth.system import SystemRandom
from tarn_adapters.config import Settings
from tarn_core.ports.identity import Mailer
from tarn_core.services.auth import AuthKit, AuthSettings


def key_manager(settings: Settings) -> RoutingKeyManager:
    """Local file keys are available only outside production; Cloud KMS (or AWS KMS) when the
    configured key reference names one."""
    local = None if settings.env == "production" else LocalKeyManager(settings.local_key_dir)
    gcp = GcpKmsKeyManager() if settings.kms_key_ref.startswith(GcpKmsKeyManager.PREFIX) else None
    aws = (
        AwsKmsKeyManager(region=settings.aws_region)
        if settings.kms_key_ref.startswith(AwsKmsKeyManager.PREFIX)
        else None
    )
    return RoutingKeyManager(local=local, gcp=gcp, aws=aws)


def build_mailer(settings: Settings, secrets: SecretSource) -> Mailer:
    """The console mailer in development; SMTP when ``TARN_MAILER=smtp``. The console mailer
    prints reset links, which are secrets, so production never gets it."""
    if settings.mailer == "smtp":
        password = settings.smtp_password.get_secret_value()
        if not password and settings.secrets_backend == "gcp" and settings.smtp_user:
            password = secrets.get(SMTP_PASSWORD).decode().strip()
        return SmtpMailer(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.smtp_from,
            user=settings.smtp_user,
            password=password,
        )
    if settings.env == "production":
        raise RuntimeError("no production mail adapter is configured (TARN_MAILER=smtp)")
    return ConsoleMailer()


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
    mailer = mailer or build_mailer(settings, secrets)
    return AuthKit(
        hasher=Argon2Hasher(secrets.get(PEPPER), previous=previous_peppers(secrets)),
        keys=key_manager(settings),
        cipher=AesGcmCipher(),
        random=SystemRandom(),
        mailer=mailer,
        common_passwords=common_passwords(),
        settings=auth_settings(settings),
    )
