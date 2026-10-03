"""P4: sign-in, lockout, sessions, reset links, recovery codes, accounts and roles."""

from dataclasses import replace

import pytest

from tarn_core.domain.audit import AuditAction, AuditEvent
from tarn_core.domain.identity import IdentityStatus
from tarn_core.errors import (
    InvariantError,
    NotFoundError,
    PasswordPolicyError,
    PermissionDeniedError,
    TokenError,
)
from tarn_core.ids import AuditEventId, AuthSessionId, CollegeId
from tarn_core.services.auth import Refreshed, Refused, ResetRequired, SignedIn
from tarn_core.services.passwords import token_college, token_session
from tarn_core.testing import InMemory
from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld

ADMIN = "admin@college-a.example"
TEACHER = "teacher@college-a.example"


@pytest.fixture
def w() -> AuthWorld:
    return AuthWorld(InMemory())


def _actions(w: AuthWorld) -> list[AuditAction]:
    return [e.action for e in w.mem.audit.events]


# --- sign-in and lockout ----------------------------------------------------------------------


def test_login_with_right_password_opens_a_session(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    out = w.auth.login(cid, "  Admin@College-A.example ", ADMIN_PASSWORD)
    assert isinstance(out, SignedIn)
    assert out.user.id == admin
    assert token_college(out.refresh_token) == cid
    assert w.mem.audit.events[-1].action is AuditAction.LOGIN


def test_wrong_password_and_unknown_email_get_the_same_refusal(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    wrong = w.auth.login(cid, ADMIN, "not the password at all")
    unknown = w.auth.login(cid, "nobody@college-a.example", ADMIN_PASSWORD)
    assert isinstance(wrong, Refused) and isinstance(unknown, Refused)
    failed = [e for e in w.mem.audit.events if e.action is AuditAction.LOGIN_FAILED]
    assert failed[-1].actor_id is None  # unknown email: no actor, no email recorded
    assert "nobody" not in str(failed[-1].after)


def test_lockout_after_n_failures_then_unlock_by_time(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    policy = w.tenant(cid).policy
    for _ in range(policy.max_failed_logins - 1):
        assert isinstance(w.auth.login(cid, ADMIN, "wrong password 1"), Refused)
    assert w.mem.identity.counters(cid, admin).failed_login_attempts == 4
    out = w.auth.login(cid, ADMIN, "wrong password 1")
    assert isinstance(out, Refused)
    assert AuditAction.LOCKED_OUT in _actions(w)
    # Locked: even the right password is refused, and nothing is verified.
    locked = w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    assert locked == Refused(reason="locked")
    w.mem.clock.advance(minutes=policy.lockout_minutes + 1)
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), SignedIn)
    assert w.mem.identity.counters(cid, admin).failed_login_attempts == 0


def test_admin_unlocks_a_teacher(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    for _ in range(5):
        w.auth.login(cid, TEACHER, "wrong password 1")
    assert w.auth.login(cid, TEACHER, TEACHER_PASSWORD) == Refused(reason="locked")
    views = {v.user.id: v for v in w.accounts.list_accounts(cid, admin)}
    assert views[teacher].locked_until is not None
    w.accounts.unlock(cid, admin, teacher)
    assert isinstance(w.auth.login(cid, TEACHER, TEACHER_PASSWORD), SignedIn)
    assert AuditAction.ACCOUNT_UNLOCKED in _actions(w)


def test_rehash_on_login_when_policy_parameters_change(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    before = w.mem.identity.get_identity(cid, admin).password_hash
    stronger = replace(w.tenant(cid).policy, argon_time_cost=4, argon_memory_cost=131072)
    w.mem.identity.save_tenant(replace(w.tenant(cid), policy=stronger))
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), SignedIn)
    after = w.mem.identity.get_identity(cid, admin).password_hash
    assert after != before and after is not None and "$4,131072,4$" in after
    # Same parameters: no rehash.
    hashed = w.mem.hasher.hashed
    w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    assert w.mem.hasher.hashed == hashed
    assert w.mem.identity.get_identity(cid, admin).password_hash == after


# --- sessions ---------------------------------------------------------------------------------


def test_remember_session_extends_the_refresh_lifetime(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    short = w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    long = w.auth.login(cid, ADMIN, ADMIN_PASSWORD, remember=True)
    assert isinstance(short, SignedIn) and isinstance(long, SignedIn)
    now = w.mem.clock.now()
    assert short.session.expires_at - now == w.mem.auth_settings.session
    assert long.session.expires_at - now == w.mem.auth_settings.remembered_session


def test_refresh_rotates_and_a_replayed_token_revokes_the_session(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    out = w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    assert isinstance(out, SignedIn)
    sid = AuthSessionId(token_session(out.refresh_token))
    first = w.auth.refresh(cid, sid, out.refresh_token)
    assert isinstance(first, Refreshed) and first.refresh_token != out.refresh_token
    replay = w.auth.refresh(cid, sid, out.refresh_token)
    assert replay == Refused(reason="reused")
    assert not w.auth.session_active(cid, sid)
    assert isinstance(w.auth.refresh(cid, sid, first.refresh_token), Refused)
    assert AuditAction.SESSION_REUSED in _actions(w)


def test_session_expires(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    out = w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    assert isinstance(out, SignedIn)
    w.mem.clock.advance(hours=13)
    sid = AuthSessionId(token_session(out.refresh_token))
    assert w.auth.refresh(cid, sid, out.refresh_token) == Refused(reason="invalid")


def test_logout_revokes(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    out = w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    assert isinstance(out, SignedIn)
    w.auth.logout(cid, admin, out.session.id)
    assert not w.auth.session_active(cid, out.session.id)


# --- reset links ------------------------------------------------------------------------------


def test_reset_link_is_single_use(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    session = w.auth.login(cid, ADMIN, ADMIN_PASSWORD)
    assert isinstance(session, SignedIn)
    w.auth.request_reset(cid, ADMIN)
    token = w.mem.mailer.last_token(ADMIN)
    # Only a hash is stored.
    assert all(token not in h for h in w.mem.identity.tokens)
    w.auth.reset_password(cid, token, "a brand new long password")
    with pytest.raises(TokenError):
        w.auth.reset_password(cid, token, "another brand new password")
    assert isinstance(w.auth.login(cid, ADMIN, "a brand new long password"), SignedIn)
    assert not w.auth.session_active(cid, session.session.id)  # reset ends old sessions


def test_reset_link_expires_after_30_minutes(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    w.auth.request_reset(cid, ADMIN)
    token = w.mem.mailer.last_token(ADMIN)
    w.mem.clock.advance(minutes=30, seconds=1)
    with pytest.raises(TokenError):
        w.auth.reset_password(cid, token, "a brand new long password")


def test_a_new_reset_link_cancels_the_old_one(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    w.auth.request_reset(cid, ADMIN)
    old = w.mem.mailer.last_token(ADMIN)
    w.auth.request_reset(cid, ADMIN)
    with pytest.raises(TokenError):
        w.auth.reset_password(cid, old, "a brand new long password")


def test_weak_new_password_keeps_the_link_usable(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    w.auth.request_reset(cid, ADMIN)
    token = w.mem.mailer.last_token(ADMIN)
    with pytest.raises(PasswordPolicyError):
        w.auth.reset_password(cid, token, "Password1234")  # on the common list
    with pytest.raises(PasswordPolicyError):
        w.auth.reset_password(cid, token, "short")
    w.auth.reset_password(cid, token, "a brand new long password")


def test_forgot_password_for_unknown_email_sends_nothing(w: AuthWorld) -> None:
    cid, _ = w.register("COLLEGE_A", ADMIN)
    sent = len(w.mem.mailer.sent)
    w.auth.request_reset(cid, "nobody@college-a.example")
    assert len(w.mem.mailer.sent) == sent


def test_reset_token_of_one_college_does_not_work_in_another(w: AuthWorld) -> None:
    a, _ = w.register("COLLEGE_A", ADMIN)
    b, _ = w.register("COLLEGE_B", "admin@college-b.example")
    w.auth.request_reset(a, ADMIN)
    token = w.mem.mailer.last_token(ADMIN)
    with pytest.raises(TokenError):
        w.auth.reset_password(b, token, "a brand new long password")


# --- recovery codes ---------------------------------------------------------------------------


def test_ten_recovery_codes_each_work_once(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    codes = w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)
    assert len(codes) == 10 and len(set(codes)) == 10
    stored = w.mem.identity.recovery_codes(cid, admin)
    assert all(code.replace("-", "") not in s.code_hash for code in codes for s in stored)
    assert w.auth.recover(cid, ADMIN, codes[3].lower(), "recovered long password") is None
    assert isinstance(w.auth.login(cid, ADMIN, "recovered long password"), SignedIn)
    again = w.auth.recover(cid, ADMIN, codes[3], "another recovered password")
    assert again == Refused(reason="code")
    assert w.auth.recovery_codes_left(cid, admin) == 9
    assert AuditAction.RECOVERY_CODE_USED in _actions(w)


def test_wrong_recovery_codes_count_towards_lockout(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    codes = w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)
    for _ in range(5):
        w.auth.recover(cid, ADMIN, "AAAA-BBBB-CCCC-DDDD", "recovered long password")
    assert w.auth.recover(cid, ADMIN, codes[0], "recovered long password") == Refused(
        reason="locked"
    )


def test_recovery_codes_need_the_current_password_and_replace_the_old_set(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    with pytest.raises(PermissionDeniedError):
        w.auth.issue_recovery_codes(cid, admin, "not my password")
    first = w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)
    w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)
    assert w.mem.identity.get_identity(cid, admin).recovery_version == 2
    assert len(w.mem.identity.recovery_codes(cid, admin)) == 10
    assert w.auth.recover(cid, ADMIN, first[0], "recovered long password") == Refused(reason="code")


# --- force reset, invitations, roles ---------------------------------------------------------


def test_force_reset_sends_the_user_to_the_reset_page(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    signed = w.auth.login(cid, TEACHER, TEACHER_PASSWORD)
    assert isinstance(signed, SignedIn)
    w.accounts.force_reset(cid, admin, teacher)
    assert not w.auth.session_active(cid, signed.session.id)
    out = w.auth.login(cid, TEACHER, TEACHER_PASSWORD)
    assert isinstance(out, ResetRequired)
    w.auth.reset_password(cid, out.reset_token, "teacher's second passphrase")
    assert isinstance(w.auth.login(cid, TEACHER, "teacher's second passphrase"), SignedIn)
    assert not w.mem.identity.get_identity(cid, teacher).force_reset


def test_invited_teacher_cannot_sign_in_before_accepting(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    user = w.accounts.invite_teacher(cid, admin, display_name="New T", email=TEACHER)
    assert w.mem.identity.get_identity(cid, user.id).status is IdentityStatus.PENDING
    assert isinstance(w.auth.login(cid, TEACHER, TEACHER_PASSWORD), Refused)
    token = w.mem.mailer.last_token(TEACHER)
    w.auth.accept_invite(cid, token, TEACHER_PASSWORD)
    assert isinstance(w.auth.login(cid, TEACHER, TEACHER_PASSWORD), SignedIn)
    with pytest.raises(TokenError):
        w.auth.accept_invite(cid, token, TEACHER_PASSWORD)


def test_admin_disables_and_enables_a_teacher(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    signed = w.auth.login(cid, TEACHER, TEACHER_PASSWORD)
    assert isinstance(signed, SignedIn)
    w.accounts.set_active(cid, admin, teacher, active=False)
    assert not w.auth.session_active(cid, signed.session.id)
    assert w.auth.login(cid, TEACHER, TEACHER_PASSWORD) == Refused(reason="account_disabled")
    w.accounts.set_active(cid, admin, teacher, active=True)
    assert isinstance(w.auth.login(cid, TEACHER, TEACHER_PASSWORD), SignedIn)


def test_teachers_cannot_manage_accounts(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    other = w.invite(cid, admin, "other@college-a.example")
    with pytest.raises(PermissionDeniedError):
        w.accounts.invite_teacher(cid, teacher, display_name="X", email="x@college-a.example")
    with pytest.raises(PermissionDeniedError):
        w.accounts.set_active(cid, teacher, other, active=False)
    with pytest.raises(PermissionDeniedError):
        w.accounts.force_reset(cid, teacher, other)
    with pytest.raises(PermissionDeniedError):
        w.accounts.unlock(cid, teacher, other)
    with pytest.raises(PermissionDeniedError):
        w.accounts.list_accounts(cid, teacher)


def test_admins_manage_teachers_only(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    with pytest.raises(PermissionDeniedError):
        w.accounts.set_active(cid, admin, admin, active=False)


def test_duplicate_email_in_a_college_is_refused(w: AuthWorld) -> None:
    from tarn_core.errors import AlreadyExistsError

    cid, admin = w.register("COLLEGE_A", ADMIN)
    w.invite(cid, admin, TEACHER)
    with pytest.raises(AlreadyExistsError):
        w.accounts.invite_teacher(cid, admin, display_name="T2", email=TEACHER.upper())


# --- across colleges --------------------------------------------------------------------------


def test_admin_of_one_college_cannot_touch_another(w: AuthWorld) -> None:
    a, admin_a = w.register("COLLEGE_A", ADMIN)
    b, admin_b = w.register("COLLEGE_B", "admin@college-b.example")
    teacher_a = w.invite(a, admin_a, TEACHER)
    with pytest.raises(NotFoundError):
        w.accounts.set_active(b, admin_b, teacher_a, active=False)
    with pytest.raises(NotFoundError):
        w.accounts.set_active(a, admin_b, teacher_a, active=False)
    # The same email may exist in two colleges; each signs in only to its own.
    w.invite(b, admin_b, TEACHER)
    assert isinstance(w.auth.login(a, TEACHER, TEACHER_PASSWORD), SignedIn)
    out = w.auth.login(a, "admin@college-b.example", ADMIN_PASSWORD)
    assert isinstance(out, Refused)


def test_a_session_of_one_college_cannot_be_refreshed_in_another(w: AuthWorld) -> None:
    a, _ = w.register("COLLEGE_A", ADMIN)
    b, _ = w.register("COLLEGE_B", "admin@college-b.example")
    out = w.auth.login(a, ADMIN, ADMIN_PASSWORD)
    assert isinstance(out, SignedIn)
    assert w.auth.refresh(b, out.session.id, out.refresh_token) == Refused(reason="unknown")


# --- audit holds no secrets ------------------------------------------------------------------


def test_no_audit_event_holds_a_password_token_or_code(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    codes = w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)
    w.auth.recover(cid, ADMIN, codes[0], "recovered long password")
    w.auth.login(cid, TEACHER, "wrong password 1")
    w.auth.request_reset(cid, TEACHER)
    token = w.mem.mailer.last_token(TEACHER)
    w.auth.reset_password(cid, token, "teacher's second passphrase")
    w.accounts.force_reset(cid, admin, teacher)
    secrets = [ADMIN_PASSWORD, TEACHER_PASSWORD, "recovered long password", token, *codes]
    secrets += [
        m.text.split("token=")[1].split()[0] for m in w.mem.mailer.sent if "token=" in m.text
    ]
    dump = " ".join(f"{e.before} {e.after}" for e in w.mem.audit.events)
    assert not [s for s in secrets if s in dump]
    assert {
        AuditAction.TENANT_REGISTERED,
        AuditAction.TENANT_EMAIL_VERIFIED,
        AuditAction.TENANT_APPROVED,
        AuditAction.ACCOUNT_CREATED,
        AuditAction.ACCOUNT_ACTIVATED,
        AuditAction.RECOVERY_CODES_ISSUED,
        AuditAction.RECOVERY_CODE_USED,
        AuditAction.LOGIN_FAILED,
        AuditAction.RESET_REQUESTED,
        AuditAction.PASSWORD_RESET,
        AuditAction.ACCOUNT_FORCE_RESET,
    } <= set(_actions(w))


def test_audit_event_refuses_secret_looking_keys() -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    with pytest.raises(InvariantError, match="no secrets"):
        AuditEvent(
            id=AuditEventId(UUID(int=1)),
            college_id=CollegeId(UUID(int=2)),
            actor_id=None,
            at=datetime.now(UTC),
            action=AuditAction.LOGIN_FAILED,
            after={"nested": {"reset_token": "x"}},
        )
    with pytest.raises(InvariantError, match="acting user"):
        AuditEvent(
            id=AuditEventId(UUID(int=1)),
            college_id=CollegeId(UUID(int=2)),
            actor_id=None,
            at=datetime.now(UTC),
            action=AuditAction.LOGIN,
        )
