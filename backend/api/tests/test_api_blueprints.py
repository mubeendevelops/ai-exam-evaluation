"""Subjects and exam blueprints over HTTP (P6): validate, create, edit as the owner, copy,
listing, roles and cross-college behaviour. In-memory backends; synthetic data."""

import copy
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
    world.register("COLLEGE_B", "admin@b.example")
    return _login(client, "COLLEGE_B", "admin@b.example", ADMIN_PASSWORD)


def _subject(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        f"{BASE}/subjects", json={"code": "PHY-501", "name": "Physics"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_subjects_are_global_and_marked_owned(
    client: TestClient, a: dict[str, str], b: dict[str, str]
) -> None:
    subject_id = _subject(client, a)
    mine = client.get(f"{BASE}/subjects", headers=a).json()
    theirs = client.get(f"{BASE}/subjects", headers=b).json()
    assert [(s["id"], s["owned"]) for s in mine] == [(subject_id, True)]
    assert [(s["id"], s["owned"]) for s in theirs] == [(subject_id, False)]
    blank = client.post(f"{BASE}/subjects", json={"code": " ", "name": "x"}, headers=a)
    assert blank.status_code == 422


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
    subject_id = _subject(client, a)
    created = client.post(f"{BASE}/blueprints", json=_example("ci", subject_id), headers=a)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["version"], body["owned"], body["total_marks"]) == (1, True, 50)
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
    subject_id = _subject(client, a)
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


def test_other_colleges_see_it_but_copy_instead_of_editing(
    client: TestClient, a: dict[str, str], b: dict[str, str]
) -> None:
    subject_id = _subject(client, a)
    original = client.post(f"{BASE}/blueprints", json=_example("ipr", subject_id), headers=a).json()

    seen = client.get(f"{BASE}/blueprints/{original['id']}", headers=b)
    assert seen.status_code == 200 and seen.json()["owned"] is False
    refused = client.put(
        f"{BASE}/blueprints/{original['id']}", json=original["document"], headers=b
    )
    assert refused.status_code == 403 and "copy it first" in refused.json()["detail"]

    copied = client.post(f"{BASE}/blueprints/{original['id']}/copy", headers=b)
    assert copied.status_code == 201
    mine = copied.json()
    assert mine["id"] != original["id"] and mine["owned"] is True and mine["version"] == 1
    assert mine["copied_from"]["id"] == original["id"]
    assert mine["document"] == original["document"]
    again = copy.deepcopy(mine["document"])
    again["title"] = "B's own wording"
    assert client.put(f"{BASE}/blueprints/{mine['id']}", json=again, headers=b).status_code == 200
    assert client.put(f"{BASE}/blueprints/{mine['id']}", json=again, headers=a).status_code == 403
    assert {x["id"] for x in client.get(f"{BASE}/blueprints", headers=a).json()} == {
        original["id"],
        mine["id"],
    }


def test_no_delete_endpoint_and_sign_in_required(client: TestClient, a: dict[str, str]) -> None:
    subject_id = _subject(client, a)
    made = client.post(f"{BASE}/blueprints", json=_example("ci", subject_id), headers=a).json()
    assert client.delete(f"{BASE}/blueprints/{made['id']}", headers=a).status_code == 405
    for method, path in (
        ("get", "/blueprints"),
        ("post", "/blueprints/validate"),
        ("post", "/subjects"),
        ("get", "/subjects"),
    ):
        assert client.request(method, BASE + path).status_code == 401
    assert client.get(f"{BASE}/blueprints/not-a-uuid", headers=a).status_code == 422
