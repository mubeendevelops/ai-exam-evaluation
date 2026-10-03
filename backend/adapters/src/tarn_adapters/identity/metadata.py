"""SQLAlchemy Core tables of the identity database (schema: migration ``i0001``; a test
checks the two agree)."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Identity,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def _uuid(name: str, *, primary_key: bool = False) -> Column[Any]:
    return Column(name, Uuid(), nullable=False, primary_key=primary_key)


def _text(name: str, *, nullable: bool = False, primary_key: bool = False) -> Column[Any]:
    return Column(name, Text(), nullable=nullable, primary_key=primary_key)


def _int(name: str, *, primary_key: bool = False) -> Column[Any]:
    return Column(name, Integer(), nullable=False, primary_key=primary_key)


def _time(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


def _bool(name: str) -> Column[Any]:
    return Column(name, Boolean(), nullable=False)


def _seq() -> Column[Any]:
    return Column("seq", BigInteger(), Identity(always=True), nullable=False)


tenants = Table(
    "tenants",
    metadata,
    _uuid("college_id", primary_key=True),
    _text("institution_id"),
    _text("name"),
    _text("kms_key_ref"),
    Column("wrapped_data_key", LargeBinary(), nullable=False),
    _int("data_key_version"),
    _int("min_password_length"),
    _int("argon_time_cost"),
    _int("argon_memory_cost"),
    _int("argon_parallelism"),
    _int("max_failed_logins"),
    _int("lockout_minutes"),
    _text("status"),
    _bool("approval_required"),
    _time("created_at"),
    _time("updated_at"),
    _time("email_verified_at", nullable=True),
    _time("approved_at", nullable=True),
    _text("approved_by", nullable=True),
    _seq(),
)

identities = Table(
    "identities",
    metadata,
    _uuid("user_id", primary_key=True),
    _uuid("college_id"),
    _text("login_handle"),
    _text("email_canonical"),
    _text("status"),
    _text("password_hash", nullable=True),
    _time("last_password_change", nullable=True),
    _bool("force_reset"),
    _time("email_verified_at", nullable=True),
    _int("recovery_version"),
    Column("other_recovery_factors", JSONB(), nullable=False),
    _time("created_at"),
    _time("updated_at"),
    _seq(),
)

login_counters = Table(
    "login_counters",
    metadata,
    _uuid("user_id", primary_key=True),
    _uuid("college_id"),
    _int("failed_login_attempts"),
    _time("lockout_until", nullable=True),
)

recovery_codes = Table(
    "recovery_codes",
    metadata,
    _uuid("college_id"),
    _uuid("user_id", primary_key=True),
    _int("ordinal", primary_key=True),
    _text("code_hash"),
    _time("created_at"),
    _time("used_at", nullable=True),
)

action_tokens = Table(
    "action_tokens",
    metadata,
    _text("token_hash", primary_key=True),
    _uuid("college_id"),
    _uuid("user_id"),
    _text("purpose"),
    _time("created_at"),
    _time("expires_at"),
    _time("used_at", nullable=True),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("user_id"),
    _text("refresh_hash"),
    _text("previous_refresh_hash", nullable=True),
    _bool("remember"),
    _time("created_at"),
    _time("expires_at"),
    _time("revoked_at", nullable=True),
)
