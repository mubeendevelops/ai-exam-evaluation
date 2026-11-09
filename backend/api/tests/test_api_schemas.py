"""Manage Schema over HTTP (P33, D219, D220): the schema list with its filters, batches and
"used in" counts, clone, generate a paper, the 1.2 fields on the blueprint routes, roles,
another subject and another college. In-memory backends; synthetic data."""

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld

BASE = "/api/v1"


def _login(client: TestClient, institution: str, email: str, password: str) -> dict[str, str]:
    response = client.post(
        f"{BASE}/auth/login",
        json={"institution_id": institution, "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def schema(subject_id: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.2",
        "kind": "schema",
        "title": title,
        "course_code": "PHY",
        "subject_id": subject_id,
        "total_marks": 10,
        "sections": [
            {
                "label": "A",
                "method": "semantic_rubric",
                "items": [
                    {"type": "question", "label": str(n), "marks": 2, "question_id": None}
                    for n in range(1, 6)
                ],
            }
        ],
        **extra,
    }


@dataclass
class College:
    sme: dict[str, str]
    other_sme: dict[str, str]
    admin: dict[str, str]
    physics: str
    chemistry: str


def _college(client: TestClient, world: AuthWorld, code: str) -> College:
    """Physics granted to the SME, chemistry to the other SME only."""
    letter = code[-1].lower()
    cid, admin_id = world.register(code, f"admin@{letter}.example")
    world.invite(cid, admin_id, f"sme@{letter}.example")
    world.invite(cid, admin_id, f"other@{letter}.example")
    admin = _login(client, code, f"admin@{letter}.example", ADMIN_PASSWORD)
    subjects = []
    for subject_code, name in (("PHY", "Physics"), ("CHE", "Chemistry")):
        made = client.post(
            f"{BASE}/subjects", json={"code": subject_code, "name": name}, headers=admin
        )
        assert made.status_code == 201, made.text
        subjects.append(str(made.json()["id"]))
    physics, chemistry = subjects
    for account in client.get(f"{BASE}/accounts", headers=admin).json():
        email, user = account["user"]["email"], account["user"]["id"]
        if email.startswith(("sme@", "other@")):
            subject = physics if email.startswith("sme@") else chemistry
            path = f"{BASE}/accounts/{user}/subjects/{subject}"
            assert client.post(path, headers=admin).status_code == 200
    return College(
        sme=_login(client, code, f"sme@{letter}.example", TEACHER_PASSWORD),
        other_sme=_login(client, code, f"other@{letter}.example", TEACHER_PASSWORD),
        admin=admin,
        physics=physics,
        chemistry=chemistry,
    )


@pytest.fixture
def a(client: TestClient, world: AuthWorld) -> College:
    return _college(client, world, "COLLEGE_A")


@pytest.fixture
def b(client: TestClient, world: AuthWorld) -> College:
    return _college(client, world, "COLLEGE_B")


def _create(client: TestClient, who: dict[str, str], document: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"{BASE}/blueprints", json=document, headers=who)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_the_schema_list_filters_counts_and_offers_batches(client: TestClient, a: College) -> None:
    matrix = _create(
        client,
        a.sme,
        schema(a.physics, "Midterm Matrix", target_batch="Class 12", weightage_note="40/60"),
    )
    _create(client, a.sme, schema(a.physics, "Board Rubric", target_batch="2024 batch"))
    _create(client, a.other_sme, schema(a.chemistry, "Chemistry Matrix", target_batch="Class 11"))
    paper = client.post(f"{BASE}/blueprints/{matrix['id']}/paper", headers=a.sme)
    assert paper.status_code == 201, paper.text

    listing = client.get(f"{BASE}/schemas", headers=a.sme).json()
    assert [c["title"] for c in listing["items"]] == ["Board Rubric", "Midterm Matrix"]
    assert listing["batches"] == ["2024 batch", "Class 12"]  # not the other subject's
    card = listing["items"][1]
    assert card["used_in"] == 1 and card["subject_name"] == "Physics"
    assert card["weightage_note"] == "40/60" and card["section_count"] == 1
    assert card["total_marks"] == 10 and card["updated_at"]

    def titles(**params: str) -> list[str]:
        found = client.get(f"{BASE}/schemas", params=params, headers=a.sme).json()
        return [c["title"] for c in found["items"]]

    assert titles(q="matrix") == ["Midterm Matrix"]
    assert titles(target_batch="class 12") == ["Midterm Matrix"]
    assert titles(q="rubric", target_batch="Class 12") == []
    # Batches stay the whole list whatever the filters.
    filtered = client.get(f"{BASE}/schemas", params={"q": "zzz"}, headers=a.sme).json()
    assert filtered == {"items": [], "batches": ["2024 batch", "Class 12"]}
    assert client.get(f"{BASE}/schemas", headers=a.admin).status_code == 403
    long = client.get(f"{BASE}/schemas", params={"q": "x" * 201}, headers=a.sme)
    assert long.status_code == 422


def test_blueprint_routes_carry_the_1_2_fields_and_the_kind(client: TestClient, a: College) -> None:
    made = _create(client, a.sme, schema(a.physics, "Matrix", target_batch="Class 12"))
    assert made["kind"] == "schema" and made["target_batch"] == "Class 12"
    assert made["document"]["schema_version"] == "1.2" and made["updated_at"]
    # A schema keeps its links empty and its kind.
    linked = schema(a.physics, "Matrix", exam_code="PHY-1")
    response = client.put(f"{BASE}/blueprints/{made['id']}", json=linked, headers=a.sme)
    assert response.status_code == 422 and "no exam code" in response.json()["detail"]
    as_paper = schema(a.physics, "Matrix", kind="paper")
    response = client.put(f"{BASE}/blueprints/{made['id']}", json=as_paper, headers=a.sme)
    assert response.status_code == 422 and "stays one" in response.json()["detail"]
    kinds = client.get(f"{BASE}/blueprints", params={"kind": "schema"}, headers=a.sme).json()
    assert [k["id"] for k in kinds] == [made["id"]]
    assert client.get(f"{BASE}/blueprints", params={"kind": "paper"}, headers=a.sme).json() == []
    bad = client.get(f"{BASE}/blueprints", params={"kind": "template"}, headers=a.sme)
    assert bad.status_code == 422


def test_clone_and_generate(client: TestClient, a: College) -> None:
    matrix = _create(client, a.sme, schema(a.physics, "Matrix", target_batch="Class 12"))
    clone = client.post(f"{BASE}/blueprints/{matrix['id']}/clone", headers=a.sme)
    assert clone.status_code == 201, clone.text
    copy = clone.json()
    assert copy["id"] != matrix["id"] and copy["version"] == 1
    assert copy["title"] == "Matrix (copy)" and copy["kind"] == "schema"
    named = client.post(
        f"{BASE}/blueprints/{matrix['id']}/clone",
        json={"title": "Board", "target_batch": "2024", "weightage_note": "50/50"},
        headers=a.sme,
    ).json()
    assert (named["title"], named["target_batch"], named["weightage_note"]) == (
        "Board",
        "2024",
        "50/50",
    )
    extra = client.post(
        f"{BASE}/blueprints/{matrix['id']}/clone", json={"owner": "x"}, headers=a.sme
    )
    assert extra.status_code == 422

    generated = client.post(f"{BASE}/blueprints/{matrix['id']}/paper", headers=a.sme)
    assert generated.status_code == 201, generated.text
    paper = generated.json()
    assert paper["kind"] == "paper" and paper["from_schema"] == matrix["id"]
    assert paper["title"] == "Matrix paper" and paper["exam_code"] == ""
    assert paper["unlinked_count"] == 5 and paper["document"]["from_schema"] == matrix["id"]
    titled = client.post(
        f"{BASE}/blueprints/{matrix['id']}/paper", json={"title": "Mid-term"}, headers=a.sme
    )
    assert titled.json()["title"] == "Mid-term"
    again = client.post(f"{BASE}/blueprints/{paper['id']}/paper", headers=a.sme)
    assert again.status_code == 409
    # A paper's own deletion check and delete work through the D202 routes.
    assert client.get(f"{BASE}/blueprints/{matrix['id']}/deletion", headers=a.sme).json() == {
        "allowed": True,
        "blockers": [],
    }
    assert client.delete(f"{BASE}/blueprints/{matrix['id']}", headers=a.sme).status_code == 204
    kept = client.get(f"{BASE}/blueprints/{paper['id']}", headers=a.sme).json()
    assert kept["from_schema"] == matrix["id"]  # the origin is kept (D231)
    assert client.get(f"{BASE}/schemas", headers=a.sme).json()["items"][0]["title"] == "Board"


def test_another_subject_and_another_college_are_not_found(
    client: TestClient, a: College, b: College
) -> None:
    chemistry = _create(client, a.other_sme, schema(a.chemistry, "Chemistry Matrix"))
    for path in ("", "/clone", "/paper"):
        method = client.get if path == "" else client.post
        mine = method(f"{BASE}/blueprints/{chemistry['id']}{path}", headers=a.sme)
        assert mine.status_code == 404, path
        theirs = method(f"{BASE}/blueprints/{chemistry['id']}{path}", headers=b.other_sme)
        assert theirs.status_code == 404, path
    for path in ("/clone", "/paper"):
        admin = client.post(f"{BASE}/blueprints/{chemistry['id']}{path}", headers=a.admin)
        assert admin.status_code == 403, path
    assert client.get(f"{BASE}/schemas", headers=b.other_sme).json()["items"] == []
    assert [
        c["title"] for c in client.get(f"{BASE}/schemas", headers=a.other_sme).json()["items"]
    ] == ["Chemistry Matrix"]
