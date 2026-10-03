"""P4: the identity database. Roles and connection rights, row-level security with crafted
SQL, atomic counters and single-use tokens under concurrency, store round trips, migrations,
and the full auth flow with real argon2id, AES-GCM and a local file key."""

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tarn_adapters.auth.crypto import AesGcmCipher, LocalKeyManager
from tarn_adapters.auth.hashing import Argon2Hasher
from tarn_adapters.auth.passwords import CommonPasswordList
from tarn_adapters.config import Settings
from tarn_adapters.identity import metadata as im
from tarn_adapters.identity import migrate as imigrate
from tarn_adapters.identity.backup import export_all, restore_file
from tarn_adapters.identity.database import IdentityDatabase
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.postgres.database import libpq_url
from tarn_adapters.postgres.testing import TestDatabase, drop_test_database
from tarn_core.domain.common import JsonValue
from tarn_core.domain.identity import (
    ActionToken,
    AuthPolicy,
    AuthSession,
    IdentityRecord,
    IdentityStatus,
    LoginCounters,
    RecoveryCode,
    TenantRecord,
    TenantStatus,
    TokenPurpose,
)
from tarn_core.errors import AlreadyExistsError, NotFoundError, TenantViolationError
from tarn_core.ids import AuthSessionId, CollegeId, UserId
from tarn_core.ports.identity import IdentityStore
from tarn_core.services.auth import AuthKit, AuthService, AuthSettings, Refused, SignedIn
from tarn_core.services.registration import RegistrationService
from tarn_core.testing import FixedClock, InMemory, MemoryMailer

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
CHEAP = AuthPolicy(argon_time_cost=1, argon_memory_cost=1024, argon_parallelism=1)
ARGON = "$argon2id$v=19$m=1024,t=1,p=1$c2FsdHNhbHQ$aGFzaGhhc2g"
TABLES = sorted(im.metadata.tables)
TENANT_TABLES = [t for t in TABLES if t != "tenants"]


def _tenant(institution: str, *, status: TenantStatus = TenantStatus.ACTIVE) -> TenantRecord:
    return TenantRecord(
        college_id=CollegeId(uuid4()),
        institution_id=institution,
        name=f"College {institution}",
        kms_key_ref="local:test",
        wrapped_data_key=b"wrapped-bytes",
        data_key_version=1,
        policy=CHEAP,
        status=status,
        approval_required=False,
        created_at=NOW,
        updated_at=NOW,
        email_verified_at=NOW if status is TenantStatus.ACTIVE else None,
    )


def _identity(college: CollegeId, email: str) -> IdentityRecord:
    return IdentityRecord(
        user_id=UserId(uuid4()),
        college_id=college,
        login_handle=email,
        email_canonical=email,
        status=IdentityStatus.ACTIVE,
        password_hash=ARGON,
        last_password_change=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _seed(db: IdentityDatabase, institution: str) -> tuple[TenantRecord, IdentityRecord]:
    """A tenant with one user and a row in every per-tenant table."""
    tenant = _tenant(institution)
    identity = _identity(tenant.college_id, f"user@{institution.lower()}.example")
    cid, uid = tenant.college_id, identity.user_id
    with db.session() as store:
        store.save_tenant(tenant)
        store.save_identity(cid, identity)
        store.set_counters(cid, uid, LoginCounters(failed_login_attempts=1))
        store.replace_recovery_codes(
            cid, uid, [RecoveryCode(ordinal=0, code_hash=ARGON, created_at=NOW)]
        )
        store.add_action_token(
            cid,
            ActionToken(
                token_hash=uuid4().hex * 2,
                college_id=cid,
                user_id=uid,
                purpose=TokenPurpose.RESET,
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=30),
            ),
        )
        store.add_session(
            cid,
            AuthSession(
                id=AuthSessionId(uuid4()),
                college_id=cid,
                user_id=uid,
                refresh_hash=uuid4().hex * 2,
                remember=False,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            ),
        )
    return tenant, identity


def _app(db: TestDatabase, college: CollegeId | None) -> psycopg.Connection[tuple[object, ...]]:
    conn = psycopg.connect(libpq_url(db.app_url))
    if college is not None:
        conn.execute("SELECT set_config('app.college_id', %s, true)", (str(college),))
    return conn


# --- roles and connection rights ------------------------------------------------------------


def test_auth_role_cannot_bypass_rls_and_every_table_forces_it(
    identity_test_database: TestDatabase,
) -> None:
    with identity_test_database.owner() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
            "WHERE rolname = 'tarn_auth'"
        ).fetchone()
        assert row == (False, False, False, False)
        owned = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
            "WHERE r.rolname = 'tarn_auth'"
        ).fetchone()
        assert owned == (0,)
        tables = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' "
            "AND relname <> 'identity_alembic_version'"
        ).fetchall()
    assert sorted(str(name) for name, *_ in tables) == TABLES
    assert all(enabled and forced for _, enabled, forced in tables)


def test_app_role_cannot_connect_to_the_identity_database(
    identity_test_database: TestDatabase, test_database: TestDatabase
) -> None:
    settings = Settings()
    from sqlalchemy.engine import make_url

    app_on_identity = (
        make_url(settings.app_database_url)
        .set(database=identity_test_database.name)
        .render_as_string(hide_password=False)
    )
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(libpq_url(app_on_identity)).close()
    auth_on_app = (
        make_url(identity_test_database.app_url)
        .set(database=test_database.name)
        .render_as_string(hide_password=False)
    )
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(libpq_url(auth_on_app)).close()


# --- row-level security with crafted SQL ---------------------------------------------------


def test_crafted_sql_sees_only_its_own_tenant(
    identity_db: IdentityDatabase, identity_test_database: TestDatabase
) -> None:
    a, _ = _seed(identity_db, f"A{uuid4().hex[:8].upper()}")
    b, _ = _seed(identity_db, f"B{uuid4().hex[:8].upper()}")
    with _app(identity_test_database, a.college_id) as conn:
        for table in TENANT_TABLES:
            own = conn.execute(
                f"SELECT count(*) FROM {table} WHERE college_id = %s",  # noqa: S608  (constant)
                (a.college_id,),
            ).fetchone()
            other = conn.execute(
                f"SELECT count(*) FROM {table} WHERE college_id = %s",  # noqa: S608
                (b.college_id,),
            ).fetchone()
            assert own is not None and int(str(own[0])) >= 1, table
            assert other == (0,), table
            changed = conn.execute(
                f"UPDATE {table} SET college_id = college_id WHERE college_id = %s",  # noqa: S608
                (b.college_id,),
            )
            assert changed.rowcount == 0, table
        # Tenants are readable (sign-in looks them up), but only A's may change.
        assert conn.execute(
            "SELECT count(*) FROM tenants WHERE college_id = %s", (b.college_id,)
        ).fetchone() == (1,)
        assert (
            conn.execute(
                "UPDATE tenants SET name = 'x' WHERE college_id = %s", (b.college_id,)
            ).rowcount
            == 0
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO login_counters (user_id, college_id) VALUES (%s, %s)",
                (uuid4(), b.college_id),
            )
        conn.rollback()
    with _app(identity_test_database, None) as conn:
        assert conn.execute("SELECT count(*) FROM identities").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM identities")
        conn.rollback()


def test_one_session_serves_one_college(identity_db: IdentityDatabase) -> None:
    a, ida = _seed(identity_db, f"A{uuid4().hex[:8].upper()}")
    b, idb = _seed(identity_db, f"B{uuid4().hex[:8].upper()}")
    with identity_db.session() as store:
        assert store.get_identity(a.college_id, ida.user_id) == ida
        with pytest.raises(TenantViolationError):
            store.get_identity(b.college_id, idb.user_id)
    with identity_db.session() as store:
        with pytest.raises(NotFoundError):
            store.get_identity(a.college_id, idb.user_id)  # B's user through A
        with pytest.raises(TenantViolationError):
            store.save_identity(a.college_id, replace(idb, login_handle="moved"))


# --- concurrency ------------------------------------------------------------------------------


def test_concurrent_failed_logins_count_exactly(identity_db: IdentityDatabase) -> None:
    tenant, identity = _seed(identity_db, f"C{uuid4().hex[:8].upper()}")
    policy = replace(CHEAP, max_failed_logins=1000)
    with identity_db.session() as store:
        store.set_counters(tenant.college_id, identity.user_id, LoginCounters())

    def fail() -> None:
        for _ in range(10):
            with identity_db.session() as store:
                store.record_failed_login(tenant.college_id, identity.user_id, NOW, policy)

    threads = [threading.Thread(target=fail) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with identity_db.session() as store:
        assert store.counters(tenant.college_id, identity.user_id).failed_login_attempts == 50


def test_lockout_threshold_in_one_statement(identity_db: IdentityDatabase) -> None:
    tenant, identity = _seed(identity_db, f"L{uuid4().hex[:8].upper()}")
    cid, uid = tenant.college_id, identity.user_id
    with identity_db.session() as store:
        store.set_counters(cid, uid, LoginCounters())
        for _ in range(4):
            assert not store.record_failed_login(cid, uid, NOW, CHEAP).locked(NOW)
        locked = store.record_failed_login(cid, uid, NOW, CHEAP)
    assert locked == LoginCounters(failed_login_attempts=0, lockout_until=NOW + CHEAP.lockout)


def test_a_token_is_used_once_even_under_a_race(identity_db: IdentityDatabase) -> None:
    tenant, identity = _seed(identity_db, f"T{uuid4().hex[:8].upper()}")
    token_hash = uuid4().hex * 2
    with identity_db.session() as store:
        store.add_action_token(
            tenant.college_id,
            ActionToken(
                token_hash=token_hash,
                college_id=tenant.college_id,
                user_id=identity.user_id,
                purpose=TokenPurpose.RESET,
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=30),
            ),
        )
    wins: list[bool] = []

    def use() -> None:
        with identity_db.session() as store:
            wins.append(
                store.use_action_token(
                    tenant.college_id, token_hash, TokenPurpose.RESET, NOW + timedelta(minutes=1)
                )
                is not None
            )

    threads = [threading.Thread(target=use) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(wins) == [False] * 7 + [True]


# --- round trips ----------------------------------------------------------------------------


def test_store_round_trips(identity_db: IdentityDatabase) -> None:
    tenant, identity = _seed(identity_db, f"R{uuid4().hex[:8].upper()}")
    cid, uid = tenant.college_id, identity.user_id
    shamir: dict[str, JsonValue] = {
        "type": "SHAMIR_SECRET_SHARE",
        "threshold": 2,
        "total_shares": 3,
        "encrypted_share_blob": "enc:v1:1:AAAA",
    }
    richer = replace(
        identity,
        recovery_version=1,
        other_recovery_factors=(shamir,),
        force_reset=True,
    )
    with identity_db.session() as store:
        store.save_identity(cid, richer)
        assert store.find_tenant(tenant.institution_id) == tenant
        assert store.get_tenant(cid) == tenant
        assert store.get_identity(cid, uid) == richer
        assert store.find_identity(cid, identity.email_canonical) == richer
        assert store.counters(cid, uid) == LoginCounters(failed_login_attempts=1)
        assert store.use_recovery_code(cid, uid, 0, NOW)
        assert not store.use_recovery_code(cid, uid, 0, NOW)
    with identity_db.session() as store, pytest.raises(AlreadyExistsError):
        store.save_tenant(_tenant(tenant.institution_id))  # same Institution ID, new tenant
    with identity_db.session() as store:
        other = _identity(cid, identity.email_canonical)
        with pytest.raises(AlreadyExistsError):
            store.save_identity(cid, other)


def test_metadata_matches_the_migrated_identity_schema(
    identity_test_database: TestDatabase,
) -> None:
    with identity_test_database.owner() as conn:
        rows = conn.execute(
            "SELECT table_name, column_name, is_nullable = 'YES' FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'identity_alembic_version'"
        ).fetchall()
    in_db = {(str(t), str(c), bool(n)) for t, c, n in rows}
    in_metadata = {
        (table.name, column.name, bool(column.nullable))
        for table in im.metadata.tables.values()
        for column in table.columns
    }
    assert in_db == in_metadata


def test_identity_migrations_up_down_up() -> None:
    settings = Settings()
    db = create_test_identity_database(settings.database_url, settings.identity_app_database_url)
    try:
        assert imigrate.current_revision(db.owner_url) == "i0002"
        imigrate.downgrade(db.owner_url, "base")
        with db.owner() as conn:
            tables = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_name <> 'identity_alembic_version'"
            ).fetchone()
        assert tables == (0,)
        imigrate.upgrade(db.owner_url)
        assert imigrate.current_revision(db.owner_url) == "i0002"
    finally:
        drop_test_database(settings.database_url, db.name)


# --- the services on the real store and real crypto ------------------------------------------


def _kit(tmp_path: Path, mailer: MemoryMailer) -> AuthKit:
    from tarn_adapters.auth.system import SystemRandom

    return AuthKit(
        hasher=Argon2Hasher(b"integration-test-pepper"),
        keys=LocalKeyManager(tmp_path / "keys"),
        cipher=AesGcmCipher(),
        random=SystemRandom(),
        mailer=mailer,
        common_passwords=CommonPasswordList(),
        settings=AuthSettings(signup_requires_approval=False, default_kms_key_ref="local:it"),
    )


def test_full_flow_with_real_crypto_and_encrypted_backup_round_trip(
    identity_db: IdentityDatabase, tmp_path: Path
) -> None:
    mem = InMemory()  # the college zone stays in memory here; the identity store is real
    kit = _kit(tmp_path, mem.mailer)
    institution = f"FLOW{uuid4().hex[:8].upper()}"
    cid = CollegeId(uuid4())
    email = "admin@flow.example"
    password = "a long synthetic passphrase"

    def services(store: IdentityStore) -> tuple[RegistrationService, AuthService]:
        reg = RegistrationService(
            identity=store, users=mem.users, colleges=mem.colleges, kit=kit, runtime=mem.runtime
        )
        auth = AuthService(identity=store, users=mem.users, kit=kit, runtime=mem.runtime)
        return reg, auth

    with identity_db.session() as store:
        reg, _ = services(store)
        reg.register(
            cid,
            institution_id=institution,
            admin_name="Admin",
            email=email,
            password=password,
            policy=CHEAP,
        )
    with identity_db.session() as store:
        reg, _ = services(store)
        assert reg.verify_email(cid, mem.mailer.last_token(email)).status is TenantStatus.ACTIVE
    with identity_db.session() as store:
        _, auth = services(store)
        assert isinstance(auth.login(cid, email, password), SignedIn)
        codes = auth.issue_recovery_codes(cid, next(iter(mem.users.list(cid))).id, password)
    stored = None
    with identity_db.session() as store:
        tenant = store.get_tenant(cid)
        stored = store.get_identity(cid, next(iter(mem.users.list(cid))).id)
        assert stored.password_hash is not None and stored.password_hash.startswith("$argon2id$")
        # Rehash when the tenant's parameters change.
        store.save_tenant(replace(tenant, policy=replace(CHEAP, argon_time_cost=2)))
    with identity_db.session() as store:
        _, auth = services(store)
        assert isinstance(auth.login(cid, email, password), SignedIn)
    with identity_db.session() as store:
        rehashed = store.get_identity(cid, stored.user_id).password_hash
        assert rehashed is not None and ",t=2," in rehashed
    with identity_db.session() as store:
        _, auth = services(store)
        assert isinstance(auth.login(cid, email, "wrong passphrase!!"), Refused)

    # Encrypted export of every tenant, then restore into a fresh identity database.
    clock = FixedClock(NOW)
    written = export_all(
        identity_db, keys=kit.keys, cipher=kit.cipher, clock=clock, out_dir=tmp_path / "backups"
    )
    mine = next(w for w in written.written if w.tenant.college_id == cid)
    assert (mine.path.stat().st_mode & 0o777) == 0o600
    assert email not in mine.path.read_text() and "argon2id" not in mine.path.read_text()
    settings = Settings()
    fresh = create_test_identity_database(settings.database_url, settings.identity_app_database_url)
    try:
        fresh_db = IdentityDatabase(fresh.app_url)
        report = restore_file(fresh_db, mine.path, keys=kit.keys, cipher=kit.cipher, clock=clock)
        assert report.identities == 1
        with identity_db.session() as old, fresh_db.session() as new:
            assert new.get_tenant(cid) == old.get_tenant(cid)
            assert new.list_identities(cid) == old.list_identities(cid)
            assert new.counters(cid, stored.user_id) == old.counters(cid, stored.user_id)
            assert new.recovery_codes(cid, stored.user_id) == old.recovery_codes(
                cid, stored.user_id
            )
        with fresh_db.session() as store:
            _, auth = services(store)
            assert isinstance(auth.login(cid, email, password), SignedIn)
            assert auth.recover(cid, email, codes[0], "another long passphrase") is None
        fresh_db.dispose()
    finally:
        drop_test_database(settings.database_url, fresh.name)
