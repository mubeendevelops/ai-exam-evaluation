"""P4: roster CSV import (validation, row errors, upsert by USN) and the picker search.
Synthetic names and USNs only."""

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.errors import NotFoundError
from tarn_core.services.roster import MAX_ROWS, parse_roster
from tarn_core.testing import InMemory
from tarn_core.testing.auth_world import AuthWorld


@pytest.fixture
def w() -> AuthWorld:
    return AuthWorld(InMemory())


GOOD = "name,usn,class/section\nAsha Rao,1ab21cs001,CSE-A\nRavi Kumar, 1AB21CS002 ,CSE-B\n"


def test_import_creates_students_with_normalised_usn(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", "admin@a.example")
    report = w.roster.import_csv(cid, admin, GOOD)
    assert report.imported and (report.created, report.updated) == (2, 0)
    asha = w.mem.students.find_by_usn(cid, "1AB21CS001")
    assert asha is not None and asha.class_section == "CSE-A"
    events = [e for e in w.mem.audit.events if e.action is AuditAction.ROSTER_IMPORTED]
    assert events[-1].after == {"rows": 2, "created": 2, "updated": 0}
    assert "Asha" not in str(events[-1].after)


def test_reimport_upserts_by_usn_and_keeps_ids(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", "admin@a.example")
    w.roster.import_csv(cid, admin, GOOD)
    before = w.mem.students.find_by_usn(cid, "1AB21CS001")
    report = w.roster.import_csv(
        cid,
        admin,
        "USN,Student Name,Section\n1AB21CS001,Asha R. Rao,CSE-C\n1AB21CS002,Ravi Kumar,CSE-B\n"
        "1AB21CS003,New Student,\n",
    )
    assert (report.created, report.updated, report.unchanged) == (1, 1, 1)
    after = w.mem.students.find_by_usn(cid, "1AB21CS001")
    assert before is not None and after is not None
    assert after.id == before.id and after.name == "Asha R. Rao" and after.class_section == "CSE-C"


def test_teachers_can_import_too(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", "admin@a.example")
    teacher = w.invite(cid, admin, "t@a.example")
    assert w.roster.import_csv(cid, teacher, GOOD).imported


def test_any_row_error_writes_nothing_and_reports_every_error(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", "admin@a.example")
    text = (
        "name,usn,class/section\n"
        "Asha Rao,1AB21CS001,CSE-A\n"
        ",1AB21CS004,CSE-A\n"  # line 3: no name
        "Bad Usn,1AB-21,CSE-A\n"  # line 4: bad USN
        "Dup,1ab21cs001,CSE-A\n"  # line 5: repeats line 2
        "No Usn,,CSE-A\n"  # line 6
        "Long Class,1AB21CS005," + "X" * 41 + "\n"  # line 7
    )
    report = w.roster.import_csv(cid, admin, text)
    assert not report.imported
    found = {(e.line, e.field) for e in report.errors}
    assert found == {(3, "name"), (4, "usn"), (5, "usn"), (6, "usn"), (7, "class_section")}
    assert "line 2" in next(e.message for e in report.errors if e.line == 5)
    assert w.mem.students.list(cid) == []


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "empty"),
        ("\n\n", "empty"),
        ("name,usn\n", "no student rows"),
        ("name,class\nAsha,CSE\n", "no 'usn'"),
        ("usn,usn,name\nX,Y,Z\n", "twice"),
        ('name,usn\n"Asha,1AB21CS001\n', "Bad CSV"),
    ],
)
def test_file_level_errors(text: str, fragment: str) -> None:
    rows, errors = parse_roster(text)
    assert not rows or errors
    assert any(fragment in e.message for e in errors), errors


def test_bom_crlf_blank_lines_quotes_and_extra_columns() -> None:
    text = (
        '﻿Name,USN,Class/Section,Phone\r\n\r\n"Rao, Asha",1ab21cs001,CSE-A,999\r\n'
        "Ravi   Kumar,1AB21CS002\r\n"
    )
    rows, errors = parse_roster(text)
    assert errors == []
    assert [(r.line, r.name, r.usn, r.class_section) for r in rows] == [
        (3, "Rao, Asha", "1AB21CS001", "CSE-A"),
        (4, "Ravi Kumar", "1AB21CS002", ""),
    ]


def test_row_limit() -> None:
    lines = ["name,usn"] + [f"S{i},USN{i:05d}" for i in range(MAX_ROWS + 1)]
    _, errors = parse_roster("\n".join(lines))
    assert any(f"More than {MAX_ROWS}" in e.message for e in errors)


def test_search_by_usn_prefix_or_name(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", "admin@a.example")
    w.roster.import_csv(cid, admin, GOOD)
    assert [s.usn for s in w.roster.search(cid, "1ab21")] == ["1AB21CS001", "1AB21CS002"]
    assert [s.name for s in w.roster.search(cid, "kum")] == ["Ravi Kumar"]
    assert len(w.roster.search(cid, "", limit=1)) == 1
    assert w.roster.search(cid, "zzz") == []


def test_roster_is_per_college(w: AuthWorld) -> None:
    a, admin_a = w.register("COLLEGE_A", "admin@a.example")
    b, admin_b = w.register("COLLEGE_B", "admin@b.example")
    w.roster.import_csv(a, admin_a, GOOD)
    assert w.roster.search(b, "") == []
    with pytest.raises(NotFoundError):
        w.roster.import_csv(b, admin_a, GOOD)  # A's admin is not a user of B
    w.roster.import_csv(b, admin_b, GOOD)  # same USNs, separate rosters
    ids_a = {s.id for s in w.mem.students.list(a)}
    ids_b = {s.id for s in w.mem.students.list(b)}
    assert ids_a.isdisjoint(ids_b)
