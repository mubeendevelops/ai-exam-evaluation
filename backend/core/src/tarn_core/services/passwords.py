"""Password policy (tenant registry ``auth_policy``) and the secrets the auth services mint.

Rules for a new password, NIST SP 800-63B style: a minimum length from the tenant policy
(12 by default), at most 256 characters, not on the common/breached list, not the user's
email or the Institution ID. No composition rules."""

import base64
import hashlib
from uuid import UUID

from tarn_core.domain.identity import AuthPolicy
from tarn_core.errors import PasswordPolicyError, TokenError
from tarn_core.ids import CollegeId
from tarn_core.ports.identity import CommonPasswords, RandomSource

MAX_PASSWORD_LENGTH = 256
RECOVERY_CODE_COUNT = 10


def password_problems(
    password: str,
    policy: AuthPolicy,
    common: CommonPasswords,
    *,
    email: str = "",
    institution_id: str = "",
) -> tuple[str, ...]:
    """Every rule the password breaks, as messages safe to show the user."""
    problems: list[str] = []
    if len(password) < policy.min_password_length:
        problems.append(f"Use at least {policy.min_password_length} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    lowered = password.lower()
    if lowered in common or lowered.strip() in common:
        problems.append("This password is too common or has appeared in a data breach.")
    personal = {email.lower(), email.lower().partition("@")[0], institution_id.lower()}
    if lowered in personal - {""}:
        problems.append("Don't use your email address or Institution ID as the password.")
    return tuple(problems)


def check_password(
    password: str,
    policy: AuthPolicy,
    common: CommonPasswords,
    *,
    email: str = "",
    institution_id: str = "",
) -> None:
    problems = password_problems(
        password, policy, common, email=email, institution_id=institution_id
    )
    if problems:
        raise PasswordPolicyError(problems)


def sha256_hex(value: str) -> str:
    """For high-entropy tokens (reset links, refresh tokens): a fast hash is enough."""
    return hashlib.sha256(value.encode()).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_link_token(random: RandomSource, college_id: CollegeId) -> str:
    """``<college hex>.<256 random bits>``: the college part lets the server open the right
    tenant before looking the token up; it is not a secret."""
    return f"{college_id.hex}.{_b64(random.token_bytes(32))}"


def new_refresh_token(random: RandomSource, college_id: CollegeId, session_id: UUID) -> str:
    return f"{college_id.hex}.{session_id.hex}.{_b64(random.token_bytes(32))}"


def token_college(token: str) -> CollegeId:
    """The college a link or refresh token belongs to. Raises TokenError if malformed."""
    head = token.partition(".")[0]
    try:
        return CollegeId(UUID(hex=head))
    except ValueError:
        raise TokenError("the link or session is not valid") from None


def token_session(token: str) -> UUID:
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("the session is not valid")
    try:
        return UUID(hex=parts[1])
    except ValueError:
        raise TokenError("the session is not valid") from None


def new_recovery_code(random: RandomSource) -> str:
    """80 random bits as ``XXXX-XXXX-XXXX-XXXX`` (base32: A-Z, 2-7)."""
    raw = base64.b32encode(random.token_bytes(10)).decode()
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


def normalise_recovery_code(raw: str) -> str:
    """What gets hashed: upper case, without spaces or dashes."""
    return "".join(ch for ch in raw.upper() if ch.isalnum())
