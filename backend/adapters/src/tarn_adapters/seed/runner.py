"""``tarn seed`` / ``make seed``: two demo colleges, the sample papers, keys and rosters.

Each college is seeded in two units of work, like an API request: the identity session and the
college session together for accounts and roster (the college session commits first), then the
college session alone for content. Run it as often as you like: what exists is left alone."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tarn_adapters.auth.secrets import secret_source
from tarn_adapters.auth.wiring import auth_kit
from tarn_adapters.blob.minio_client import make_client
from tarn_adapters.blob.minio_store import MinioBlobStore
from tarn_adapters.config import Settings
from tarn_adapters.identity.database import IdentityDatabase
from tarn_adapters.postgres.database import PostgresDatabase, PostgresSession
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_core.ids import CollegeId
from tarn_core.ports.storage import BlobStore
from tarn_core.seed import (
    COLLEGES,
    DEV_PASSWORD,
    SeededAccounts,
    SeedError,
    SeedReport,
    seed_accounts,
    seed_content,
)
from tarn_core.seed.model import CollegeSeed
from tarn_core.services.blueprints import BlueprintService
from tarn_core.services.question_bank import QuestionBankService
from tarn_core.services.subjects import SubjectService

DATA = Path(__file__).parent / "data"


def asset_reader(directory: Path = DATA) -> Callable[[str], bytes]:
    """Bytes of a seed asset by file name; only plain names inside ``directory``."""

    def read(name: str) -> bytes:
        path = directory / name
        if path.parent != directory or not path.is_file():
            raise SeedError(f"seed asset {name!r} is missing")
        return path.read_bytes()

    return read


@dataclass(frozen=True, slots=True)
class SeedSummary:
    report: SeedReport
    colleges: tuple[tuple[CollegeSeed, SeededAccounts], ...]
    password: str


def run_seed(settings: Settings, *, blobs: BlobStore | None = None) -> SeedSummary:
    """Seed the database the settings point at. Refuses in production: the accounts share a
    published development password. ``blobs`` replaces MinIO (tests)."""
    if settings.env == "production":
        raise SeedError("the development seed does not run when TARN_ENV=production")
    report = SeedReport()
    ids = UuidGenerator()
    clock = SystemClock()
    secrets = secret_source(settings)
    kit = auth_kit(settings, secrets=secrets)
    blobs = blobs or MinioBlobStore(make_client(settings), settings.blob_bucket)
    app = PostgresDatabase(settings.app_database_url)
    identity_db = IdentityDatabase(settings.identity_app_database_url)
    assets = asset_reader()
    seeded: list[tuple[CollegeSeed, SeededAccounts]] = []
    try:
        for college in COLLEGES:
            with identity_db.session() as store:
                tenant = store.find_tenant(college.institution_id)
            college_id = tenant.college_id if tenant else CollegeId(ids.new())

            with (
                identity_db.session() as store,
                app.session(college_id, ids=ids, clock=clock, blobs=blobs) as scope,
            ):
                accounts = seed_accounts(
                    college,
                    college_id=college_id,
                    tenant=tenant,
                    password=DEV_PASSWORD,
                    identity=store,
                    users=scope.users,
                    colleges=scope.colleges,
                    students=scope.students,
                    kit=kit,
                    runtime=scope.runtime,
                    report=report,
                )

            with app.session(college_id, ids=ids, clock=clock, blobs=blobs) as scope:
                _content(college, accounts, scope, assets, report)
            seeded.append((college, accounts))
    finally:
        app.dispose()
        identity_db.dispose()
    return SeedSummary(report=report, colleges=tuple(seeded), password=DEV_PASSWORD)


def _content(
    college: CollegeSeed,
    accounts: SeededAccounts,
    scope: PostgresSession,
    assets: Callable[[str], bytes],
    report: SeedReport,
) -> None:
    seed_content(
        college,
        college_id=accounts.college_id,
        question_author=accounts.teacher_ids[0],
        paper_author=accounts.teacher_ids[1],
        content=scope.content,
        bank=QuestionBankService(
            content=scope.content,
            users=scope.users,
            colleges=scope.colleges,
            blobs=scope.blobs,
            runtime=scope.runtime,
        ),
        subjects=SubjectService(content=scope.content, users=scope.users, runtime=scope.runtime),
        blueprints=BlueprintService(
            content=scope.content, users=scope.users, runtime=scope.runtime
        ),
        assets=assets,
        report=report,
    )
