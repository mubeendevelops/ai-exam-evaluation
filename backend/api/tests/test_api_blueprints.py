"""Subjects and exam blueprints over HTTP (P6): validate, create, edit, listing, roles and
college privacy (D165). In-memory backends; synthetic data."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld

DOCS = Path(__file__).resolve().parents[3] / "docs" / "api"
BASE = "/api/v1"


def _example(name: str, subject_id: str | None = None) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((DOCS / f"blueprint.example-{name}.json").read_text())
    if subject_id:
        document["subject_id"] = subject_id
    return document


def _login(client: TestClient, institution: str, email: str, password: str) -> dict[str, str]:
    response = client.post(
        f"{BASE}/auth/login",
        json={"institution_id": institution, "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def a(client: TestClient, world: AuthWorld) -> dict[str, str]:
    cid, admin = world.register("COLLEGE_A", "admin@a.example")
    world.invite(cid, admin, "teacher@a.example")
    return _login(client, "COLLEGE_A", "teacher@a.example", TEACHER_PASSWORD)


@pytest.fixture
def b(client: TestClient, world: AuthWorld) -> dict[str, str]:
    """College B's teacher."""
    cid, admin = world.register("COLLEGE_B", "admin@b.example")
    world.invite(cid, admin, "teacher@b.example")
    return _login(client, "COLLEGE_B", "teacher@b.example", TEACHER_PASSWORD)


@pytest.fixture
def a_admin(client: TestClient, a: dict[str, str]) -> dict[str, str]:
    return _login(client, "COLLEGE_A", "admin@a.example", ADMIN_PASSWORD)


def _admin(client: TestClient, institution: str) -> dict[str, str]:
    email = f"admin@{institution[-1].lower()}.example"
    return _login(client, institution, email, ADMIN_PASSWORD)


def _subject(
    client: TestClient, institution: str, code: str = "PHY-501", name: str = "Physics"
) -> str:
    """Made by the college's admin and granted to each of its SMEs (D160)."""
    admin = _admin(client, institution)
    response = client.post(f"{BASE}/subjects", json={"code": code, "name": name}, headers=admin)
    assert response.status_code == 201, response.text
    subject = str(response.json()["id"])
    for account in client.get(f"{BASE}/accounts", headers=admin).json():
        if account["user"]["role"] == "sme":
            path = f"{BASE}/accounts/{account['user']['id']}/subjects/{subject}"
            assert client.post(path, headers=admin).status_code == 200
    return subject


def test_subjects_belong_to_the_college(
    client: TestClient, a: dict[str, str], b: dict[str, str]
) -> None:
    subject_id = _subject(client, "COLLEGE_A")
    mine = client.get(f"{BASE}/subjects", headers=a).json()
    theirs = client.get(f"{BASE}/subjects", headers=b).json()
    assert [s["id"] for s in mine] == [subject_id] and "owned" not in mine[0]
    assert theirs == []
    other = _subject(client, "COLLEGE_B")  # the same code in another college is its own subject
    assert other != subject_id
    admin = _admin(client, "COLLEGE_A")
    blank = client.post(f"{BASE}/subjects", json={"code": " ", "name": "x"}, headers=admin)
    assert blank.status_code == 422
    taken = client.post(f"{BASE}/subjects", json={"code": "phy-501", "name": "x"}, headers=admin)
    assert taken.status_code == 409
    assert (
        client.post(f"{BASE}/subjects", json={"code": "S", "name": "S"}, headers=a).status_code
        == 403
    )


def test_the_admin_renames_and_retires_subjects(
    client: TestClient, a: dict[str, str], world: AuthWorld
) -> None:
    physics = _subject(client, "COLLEGE_A")
    maths = _subject(client, "COLLEGE_A", "MAT", "Maths")
    admin = _admin(client, "COLLEGE_A")
    renamed = client.put(
        f"{BASE}/subjects/{physics}", json={"code": "PHY-502", "name": "Physics II"}, headers=admin
    )
    assert renamed.status_code == 200 and renamed.json()["version"] == 2
    assert renamed.json()["name"] == "Physics II"
    assert (
        client.put(
            f"{BASE}/subjects/{physics}", json={"code": "X", "name": "X"}, headers=a
        ).status_code
        == 403
    )
    retired = client.post(f"{BASE}/subjects/{maths}/retire", headers=admin)
    assert retired.status_code == 200 and retired.json()["retired"] is True
    # The SME no longer picks it, nor can a new paper use it; the admin still lists it.
    assert [x["code"] for x in client.get(f"{BASE}/subjects", headers=a).json()] == ["PHY-502"]
    assert {x["code"] for x in client.get(f"{BASE}/subjects", headers=admin).json()} == {
        "PHY-502",
        "MAT",
    }
    sme = client.get(f"{BASE}/auth/me", headers=a).json()["user"]["id"]
    regrant = client.post(f"{BASE}/accounts/{sme}/subjects/{maths}", headers=admin)
    assert regrant.status_code == 422 and "retired" in regrant.json()["detail"]
    paper = client.post(f"{BASE}/blueprints", json=_example("ci", maths), headers=a)
    assert paper.status_code == 422 and "retired" in paper.json()["detail"]
    assert client.post(f"{BASE}/subjects/{maths}/retire", headers=a).status_code == 403


def test_validate_reports_every_problem_and_the_totals(
    client: TestClient, a: dict[str, str]
) -> None:
    ok = client.post(f"{BASE}/blueprints/validate", json=_example("ipr"), headers=a).json()
    assert ok["valid"] is True and ok["issues"] == []
    assert ok["computed_total"] == 60 and ok["question_count"] == 13
    assert [(s["label"], s["items"], s["counted"], s["max_marks"]) for s in ok["sections"]] == [
        ("A", 7, 5, 15),
        ("B", 4, 3, 30),
        ("C", 1, 1, 15),
    ]
    assert len(ok["unlinked"]) == 15

    broken = _example("ci")
    broken["total_marks"] = 60
    broken["sections"][0]["choice"]["n"] = 9
    broken["sections"][1]["items"][0]["marks"] = 0
    bad = client.post(f"{BASE}/blueprints/validate", json=broken, headers=a)
    assert bad.status_code == 200
    body = bad.json()
    assert body["valid"] is False
    paths = {i["path"] for i in body["issues"]}
    assert {"sections[0].choice.n", "sections[1].items[0].marks"} <= paths
    assert any("impossible" in i["message"] for i in body["issues"])

    objective = _example("ci")
    objective["negative_marking"] = 0.5
    warned = client.post(f"{BASE}/blueprints/validate", json=objective, headers=a).json()
    assert warned["valid"] is True and [w["path"] for w in warned["warnings"]] == [
        "negative_marking"
    ]


def test_create_read_edit_versions(client: TestClient, a: dict[str, str]) -> None:
    subject_id = _subject(client, "COLLEGE_A")
    created = client.post(f"{BASE}/blueprints", json=_example("ci", subject_id), headers=a)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["version"], body["total_marks"]) == (1, 50)
    assert "owned" not in body and "copied_from" not in body
    assert body["subject_name"] == "Physics" and body["unlinked_count"] == 17
    assert body["document"] == _example("ci", subject_id)  # the document comes back unchanged

    edited = _example("ci", subject_id)
    edited["title"] = "Version two"
    put = client.put(f"{BASE}/blueprints/{body['id']}", json=edited, headers=a)
    assert put.status_code == 200 and put.json()["version"] == 2

    assert client.get(f"{BASE}/blueprints/{body['id']}", headers=a).json()["title"] == "Version two"
    first = client.get(f"{BASE}/blueprints/{body['id']}?version=1", headers=a).json()
    assert first["title"] == "Synthetic paper, CI shape"
    versions = client.get(f"{BASE}/blueprints/{body['id']}/versions", headers=a).json()
    assert [v["version"] for v in versions] == [1, 2]
    listing = client.get(f"{BASE}/blueprints", headers=a).json()
    assert [(x["id"], x["version"]) for x in listing] == [(body["id"], 2)]
    assert client.get(f"{BASE}/blueprints/{body['id']}?version=9", headers=a).status_code == 404


def test_an_invalid_blueprint_is_refused_with_all_reasons(
    client: TestClient, a: dict[str, str]
) -> None:
    subject_id = _subject(client, "COLLEGE_A")
    broken = _example("ci", subject_id)
    broken["total_marks"] = 49
    broken["sections"][2]["items"][1]["marks"] = 9
    response = client.post(f"{BASE}/blueprints", json=broken, headers=a)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "total_marks" in detail  # sections add up to a different number now
    assert client.get(f"{BASE}/blueprints", headers=a).json() == []

    unknown = client.post(
        f"{BASE}/blueprints",
        json=_example("ci", "00000000-0000-4000-8000-0000000000ee"),
        headers=a,
    )
    assert unknown.status_code == 422 and "no such subject" in unknown.json()["detail"]


def test_another_college_finds_nothing_and_there_is_no_copy(
    client: TestClient, a: dict[str, str], b: dict[str, str]
) -> None:
    subject_id = _subject(client, "COLLEGE_A")
    original = client.post(f"{BASE}/blueprints", json=_example("ipr", subject_id), headers=a).json()
    path = f"{BASE}/blueprints/{original['id']}"

    assert client.get(path, headers=b).status_code == 404
    assert client.get(f"{path}/versions", headers=b).status_code == 404
    assert client.put(path, json=original["document"], headers=b).status_code == 404
    assert client.post(f"{path}/copy", headers=b).status_code in (404, 405)
    assert client.get(f"{BASE}/blueprints", headers=b).json() == []
    # B's paper on A's subject: the subject is not found in B.
    refused = client.post(f"{BASE}/blueprints", json=original["document"], headers=b)
    assert refused.status_code == 422 and "no such subject" in refused.json()["detail"]
    assert client.get(path, headers=a).json()["version"] == 1


def test_smes_author_papers_the_admin_does_not_open_them(
    client: TestClient, a: dict[str, str], a_admin: dict[str, str]
) -> None:
    subject_id = _subject(client, "COLLEGE_A")
    made = client.post(f"{BASE}/blueprints", json=_example("ci", subject_id), headers=a).json()
    assert client.get(f"{BASE}/blueprints/{made['id']}", headers=a_admin).status_code == 403
    edit = client.put(f"{BASE}/blueprints/{made['id']}", json=made["document"], headers=a_admin)
    assert edit.status_code == 403 and "role" in edit.json()["detail"]
    create = client.post(f"{BASE}/blueprints", json=made["document"], headers=a_admin)
    assert create.status_code == 403


def test_unused_paper_deletes_and_sign_in_required(client: TestClient, a: dict[str, str]) -> None:
    # P28 (D166): a paper no unit uses can be deleted for good.
    subject_id = _subject(client, "COLLEGE_A")
    made = client.post(f"{BASE}/blueprints", json=_example("ci", subject_id), headers=a).json()
    check = client.get(f"{BASE}/blueprints/{made['id']}/deletion", headers=a).json()
    assert check == {"allowed": True, "blockers": []}
    assert client.delete(f"{BASE}/blueprints/{made['id']}", headers=a).status_code == 204
    assert client.get(f"{BASE}/blueprints/{made['id']}", headers=a).status_code == 404
    for method, path in (
        ("get", "/blueprints"),
        ("post", "/blueprints/validate"),
        ("post", "/subjects"),
        ("get", "/subjects"),
    ):
        assert client.request(method, BASE + path).status_code == 401
    assert client.get(f"{BASE}/blueprints/not-a-uuid", headers=a).status_code == 422
