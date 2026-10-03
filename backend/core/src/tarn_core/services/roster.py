"""Roster CSV import and the student picker's search (P4).

The CSV has a header row with ``name``, ``usn`` and optionally ``class/section`` (aliases
below, any case). Rows are validated first; if any row is wrong nothing is written and every
error is reported with its line number. Otherwise students are upserted by USN: a known USN
keeps its id and gets the new name and class/section."""

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.tenancy import Student, normalise_usn
from tarn_core.errors import PermissionDeniedError
from tarn_core.ids import CollegeId, StudentId, UserId
from tarn_core.ports.repositories import StudentRepository, UserRepository
from tarn_core.services._support import Runtime

MAX_ROWS = 5000
MAX_CHARS = 1_000_000
MAX_NAME = 200
MAX_CLASS_SECTION = 40
SEARCH_LIMIT = 50
_USN = re.compile(r"[A-Z0-9]{4,20}")

_HEADERS = {
    "name": "name",
    "student name": "name",
    "full name": "name",
    "usn": "usn",
    "class/section": "class_section",
    "class section": "class_section",
    "class-section": "class_section",
    "class": "class_section",
    "section": "class_section",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class RowError:
    line: int
    """1-based line in the file (0: the file as a whole)."""
    field: str
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterRow:
    line: int
    name: str
    usn: str
    class_section: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterReport:
    rows: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: tuple[RowError, ...] = field(default=())

    @property
    def imported(self) -> bool:
        return not self.errors


def _header_key(raw: str) -> str:
    return " ".join(raw.replace("_", " ").strip().lower().split())


def parse_roster(text: str) -> tuple[list[RosterRow], list[RowError]]:
    """Pure parsing and validation; no repository access."""
    errors: list[RowError] = []
    if len(text) > MAX_CHARS:
        return [], [RowError(line=0, field="file", message="The file is larger than 1 MB.")]
    text = text.removeprefix("﻿")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    columns: dict[str, int] | None = None
    rows: list[RosterRow] = []
    seen: dict[str, int] = {}
    data_rows = 0
    try:
        for record in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in record):
                continue
            if columns is None:
                columns = {}
                for index, cell in enumerate(record):
                    key = _HEADERS.get(_header_key(cell))
                    if key is None:
                        continue  # extra columns are ignored
                    if key in columns:
                        errors.append(
                            RowError(line=line, field=key, message="This column appears twice.")
                        )
                    columns[key] = index
                for required in ("name", "usn"):
                    if required not in columns:
                        errors.append(
                            RowError(
                                line=line,
                                field=required,
                                message=f"The header row has no '{required}' column.",
                            )
                        )
                if errors:
                    return [], errors
                continue
            data_rows += 1
            if data_rows > MAX_ROWS:
                errors.append(
                    RowError(line=line, field="file", message=f"More than {MAX_ROWS} rows.")
                )
                break
            row_errors, row = _check_row(line, record, columns, seen)
            errors += row_errors
            if row is not None:
                rows.append(row)
    except csv.Error as exc:
        errors.append(RowError(line=reader.line_num, field="file", message=f"Bad CSV: {exc}"))
    if columns is None:
        errors.append(RowError(line=0, field="file", message="The file is empty."))
    elif not rows and not errors:
        errors.append(RowError(line=0, field="file", message="The file has no student rows."))
    return rows, errors


def _check_row(
    line: int, record: list[str], columns: dict[str, int], seen: dict[str, int]
) -> tuple[list[RowError], RosterRow | None]:
    def cell(key: str) -> str:
        index = columns.get(key)
        if index is None or index >= len(record):
            return ""
        return " ".join(record[index].split())

    errors: list[RowError] = []
    name = cell("name")
    usn = normalise_usn(cell("usn"))
    class_section = cell("class_section")
    if not name:
        errors.append(RowError(line=line, field="name", message="Name is empty."))
    elif len(name) > MAX_NAME:
        errors.append(RowError(line=line, field="name", message=f"Name is longer than {MAX_NAME}."))
    if not usn:
        errors.append(RowError(line=line, field="usn", message="USN is empty."))
    elif not _USN.fullmatch(usn):
        errors.append(
            RowError(
                line=line,
                field="usn",
                message="USN must be 4 to 20 letters and digits.",
            )
        )
    elif usn in seen:
        errors.append(RowError(line=line, field="usn", message=f"USN repeats line {seen[usn]}."))
    else:
        seen[usn] = line
    if len(class_section) > MAX_CLASS_SECTION:
        errors.append(
            RowError(
                line=line,
                field="class_section",
                message=f"Class/section is longer than {MAX_CLASS_SECTION}.",
            )
        )
    if errors:
        return errors, None
    return [], RosterRow(line=line, name=name, usn=usn, class_section=class_section)


class RosterService:
    """Admins and teachers of the college may import and search the roster."""

    def __init__(
        self, *, students: StudentRepository, users: UserRepository, runtime: Runtime
    ) -> None:
        self._students = students
        self._users = users
        self._rt = runtime

    def import_csv(self, college_id: CollegeId, actor_id: UserId, text: str) -> RosterReport:
        actor = self._users.get(college_id, actor_id)
        if not actor.active:
            raise PermissionDeniedError("the account is disabled")
        rows, errors = parse_roster(text)
        if errors:
            return RosterReport(rows=len(rows), errors=tuple(errors))
        created = updated = unchanged = 0
        for row in rows:
            existing = self._students.find_by_usn(college_id, row.usn)
            if existing is None:
                self._students.save(
                    college_id,
                    Student(
                        id=self._rt.new_id(StudentId),
                        college_id=college_id,
                        name=row.name,
                        usn=row.usn,
                        class_section=row.class_section,
                    ),
                )
                created += 1
            elif (existing.name, existing.class_section) != (row.name, row.class_section):
                self._students.save(
                    college_id, replace(existing, name=row.name, class_section=row.class_section)
                )
                updated += 1
            else:
                unchanged += 1
        report = RosterReport(rows=len(rows), created=created, updated=updated, unchanged=unchanged)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.ROSTER_IMPORTED,
            after={"rows": report.rows, "created": created, "updated": updated},
        )
        return report

    def search(self, college_id: CollegeId, query: str, limit: int = 20) -> Sequence[Student]:
        return self._students.search(college_id, query, max(1, min(limit, SEARCH_LIMIT)))
