"""``tarn content relocate-blobs`` (P23, D165): move content files from ``global/...`` to
``college/{id}/content/...`` and update the rows that name them.

Runs as the owner role (``TARN_DATABASE_URL``): the application role may not update content
rows (they are append-only). Every college is handled in its own transaction with
``app.college_id`` set, so row-level security holds for a non-superuser owner too. The new key
is the old one with ``global/`` replaced by ``college/{id}/content/``, so a second run finds
nothing to do and a run that stopped half-way picks up where it stopped. An old object is
deleted only after every college's rows have moved off it."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Column, ColumnElement, Table, create_engine, literal, select, text, update

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.database import sqlalchemy_url
from tarn_core.domain.common import BlobKey
from tarn_core.domain.content import content_blob_key
from tarn_core.ids import CollegeId
from tarn_core.ports.storage import BlobStore

LEGACY_PREFIX = "global/"

# table, its key column and the stored file's media type
_TABLES: tuple[tuple[Table, Column[str], ColumnElement[str]], ...] = (
    (m.reference_diagrams, m.reference_diagrams.c.png_key, literal("image/png")),
    (m.key_files, m.key_files.c.blob_key, m.key_files.c.media_type),
)


@dataclass(frozen=True, slots=True)
class Relocation:
    colleges: int
    copied: int
    """Objects written under the college's content folder."""
    rows: int
    """Content rows (every version) whose key was updated."""
    missing: int
    """Rows whose old object was not found: the key is updated anyway, the file is lost."""
    deleted: int
    """Old ``global/`` objects removed."""


def new_key(college_id: CollegeId, old: str) -> BlobKey:
    if not old.startswith(LEGACY_PREFIX):
        raise ValueError("not a legacy content key")
    return content_blob_key(college_id, *old[len(LEGACY_PREFIX) :].split("/"))


def relocate_content_blobs(
    owner_url: str, blobs: BlobStore, progress: Callable[[str], None] = lambda _m: None
) -> Relocation:
    engine = create_engine(sqlalchemy_url(owner_url), pool_size=1)
    copied = rows = missing = 0
    moved: set[str] = set()
    try:
        with engine.connect() as conn:
            directory = m.college_directory.c.id
            listed = conn.execute(select(directory).order_by(directory))
            colleges = [CollegeId(r[0]) for r in listed]
        for college_id in colleges:
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.college_id', :c, true)"), {"c": str(college_id)}
                )
                for table, column, media in _TABLES:
                    found = conn.execute(
                        select(column, media)
                        .distinct()
                        .where(table.c.college_id == college_id, column.startswith(LEGACY_PREFIX))
                    ).fetchall()
                    for old, media_type in found:
                        target = new_key(college_id, old)
                        source = BlobKey(old)
                        if not blobs.exists(target):
                            if blobs.exists(source):
                                blobs.put(target, blobs.get(source), media_type)
                                copied += 1
                            else:
                                missing += 1
                        updated = conn.execute(
                            update(table)
                            .where(table.c.college_id == college_id, column == old)
                            .values({column: target.value})
                        )
                        rows += updated.rowcount
                        moved.add(old)
                        progress(f"{table.name}: {old} -> {target.value}")
        deleted = 0
        for old in sorted(moved):
            source = BlobKey(old)
            if blobs.exists(source):
                blobs.delete(source)
                deleted += 1
    finally:
        engine.dispose()
    return Relocation(
        colleges=len(colleges), copied=copied, rows=rows, missing=missing, deleted=deleted
    )
