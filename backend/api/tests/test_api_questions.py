"""The question bank over HTTP (P7): weights, edit vs copy, uploads, filters, downloads.
In-memory backends; synthetic data."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld

BASE = "/api/v1"
PDF = b"%PDF-1.7\nsynthetic key"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


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


@pytest.fixture
def subject(client: TestClient, a: dict[str, str]) -> str:
    response = client.post(f"{BASE}/subjects", json={"code": "PHY", "name": "Physics"}, headers=a)
    return str(response.json()["id"])


def semantic(label: str, weight: float, **extra: Any) -> dict[str, Any]:
    return {
        "type": "semantic",
        "label": label,
        "weight": weight,
        "params": {"reference_statement": f"{label} statement"},
        **extra,
    }


def listed(weight: float) -> dict[str, Any]:
    return {
        "type": "list",
        "label": "Names the items",
        "weight": weight,
        "params": {
            "items": [{"term": "alpha", "synonyms": ["a"]}, {"term": "beta", "synonyms": []}],
            "required_count": 2,
        },
    }


def body(subject: str, code: str = "PHY-Q1", **kw: Any) -> dict[str, Any]:
    return {
        "subject_id": subject,
        "code": code,
        "text": f"Explain {code}.",
        "max_marks": 4,
        "difficulty": "medium",
        "category": "Optics",
        **kw,
    }


def make(client: TestClient, headers: dict[str, str], subject: str, **kw: Any) -> dict[str, Any]:
    response = client.post(f"{BASE}/questions", json=body(subject, **kw), headers=headers)
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


def upload(
    client: TestClient, headers: dict[str, str], qid: str, data: bytes, ctype: str, **params: Any
) -> Any:
    params.setdefault("filename", "key.pdf")
    params.setdefault("confirm_no_student_data", "true")
    return client.post(
        f"{BASE}/questions/{qid}/key-files",
        params=params,
        content=data,
        headers={**headers, "Content-Type": ctype},
    )


# --- weights ----------------------------------------------------------------------------------


def test_create_with_key_and_rubric_and_read_it_back(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(
        client,
        a,
        subject,
        reference_answer="The model answer.",
        criteria=[listed(2.5), semantic("Explains", 1.5)],
    )
    assert q["owned"] is True and q["owner_name"] and q["subject_name"] == "Physics"
    assert q["key_count"] == 1 and q["version"] == 1
    assert q["rubric"]["complete"] is True and q["rubric"]["total"] == 4
    kinds = [c["criterion"]["type"] for c in q["rubric"]["criteria"]]
    assert kinds == ["list", "semantic"]
    assert q["rubric"]["criteria"][0]["criterion"]["params"]["required_count"] == 2
    assert [x["text"] for x in q["reference_answers"]] == ["The model answer."]
    again = client.get(f"{BASE}/questions/{q['id']}", headers=a).json()
    assert again == q


def test_weights_that_do_not_add_up_are_refused_and_nothing_is_saved(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    bad = client.post(
        f"{BASE}/questions",
        json=body(subject, criteria=[listed(2), semantic("Explains", 1.5)]),
        headers=a,
    )
    assert bad.status_code == 422
    assert "add up to 3.5 marks, but the question carries 4" in bad.json()["detail"]
    assert client.get(f"{BASE}/questions", headers=a).json()["total"] == 0


def test_rubric_replacement_numeric_and_diagram_criteria(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject, max_marks=6)
    diagram = client.post(
        f"{BASE}/questions/{q['id']}/diagrams",
        params={"filename": "flow.png", "confirm_no_student_data": "true"},
        content=PNG,
        headers={**a, "Content-Type": "image/png"},
    )
    assert diagram.status_code == 201, diagram.text
    did = diagram.json()["id"]
    criteria = [
        {
            "type": "numeric",
            "label": "Step 1",
            "weight": 1,
            "params": {"expected": 2.5, "tolerance": 0.1, "unit": "V"},
        },
        {"type": "numeric", "label": "Step 2", "weight": 1, "params": {"expected": 7}},
        {
            "type": "diagram",
            "label": "Nodes",
            "weight": 1,
            "params": {"reference_diagram_id": did, "component": "nodes"},
        },
        {
            "type": "diagram",
            "label": "Edges",
            "weight": 1,
            "params": {"reference_diagram_id": did, "component": "edges"},
        },
        {
            "type": "diagram",
            "label": "Labels",
            "weight": 1,
            "params": {"reference_diagram_id": did, "component": "labels"},
        },
        semantic("Concludes", 1),
    ]
    put = client.put(f"{BASE}/questions/{q['id']}/rubric", json={"criteria": criteria}, headers=a)
    assert put.status_code == 200, put.text
    rubric = put.json()
    assert rubric["total"] == 6 and rubric["complete"] is True
    assert rubric["criteria"][0]["criterion"]["params"] == {
        "expected": 2.5,
        "tolerance": 0.1,
        "unit": "V",
    }
    # A change of weights that no longer adds up is refused; the rubric stays as it was.
    ids = [c["criterion"]["id"] for c in rubric["criteria"]]
    skewed = [{**c, "id": i} for c, i in zip(criteria, ids, strict=True)]
    skewed[0]["weight"] = 3
    refused = client.put(f"{BASE}/questions/{q['id']}/rubric", json={"criteria": skewed}, headers=a)
    assert refused.status_code == 422 and "add up to 8 marks" in refused.json()["detail"]
    assert client.get(f"{BASE}/questions/{q['id']}", headers=a).json()["rubric"] == rubric


def test_marks_and_rubric_change_together(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject, criteria=[semantic("Only", 4)])
    edit = {
        "code": q["code"],
        "text": q["text"],
        "max_marks": 6,
        "difficulty": "hard",
        "category": "Optics",
    }
    refused = client.put(f"{BASE}/questions/{q['id']}", json=edit, headers=a)
    assert refused.status_code == 422 and "Change the rubric together" in refused.json()["detail"]
    cid = q["rubric"]["criteria"][0]["criterion"]["id"]
    ok = client.put(
        f"{BASE}/questions/{q['id']}",
        json={**edit, "criteria": [semantic("Only", 6, id=cid)]},
        headers=a,
    )
    assert ok.status_code == 200, ok.text
    assert (ok.json()["version"], ok.json()["max_marks"], ok.json()["difficulty"]) == (2, 6, "hard")
    assert ok.json()["rubric"]["complete"] is True


def test_bad_parameters_are_422(client: TestClient, a: dict[str, str], subject: str) -> None:
    for criteria in (
        [{**listed(4), "params": {"items": [{"term": "a"}], "required_count": 3}}],
        [
            {
                "type": "numeric",
                "label": "n",
                "weight": 4,
                "params": {"expected": 1, "tolerance": -1},
            }
        ],
        [{"type": "llm", "label": "n", "weight": 4, "params": {"instructions": "x"}}],
        [semantic("x", 0)],
    ):
        response = client.post(
            f"{BASE}/questions", json=body(subject, criteria=criteria), headers=a
        )
        assert response.status_code == 422, criteria


# --- edit vs copy -----------------------------------------------------------------------------


def test_the_owner_edits_everyone_else_copies(
    client: TestClient, a: dict[str, str], b: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject, reference_answer="Model.", criteria=[semantic("All", 4)])
    assert upload(client, a, q["id"], PDF, "application/pdf", keywords=["lens"]).status_code == 201
    qid = q["id"]
    edit = {
        "code": q["code"],
        "text": "Hijack",
        "max_marks": 4,
        "difficulty": "easy",
        "category": "",
    }

    seen = client.get(f"{BASE}/questions/{qid}", headers=b).json()
    assert seen["owned"] is False and seen["owner_name"]
    attempts: list[tuple[str, str, dict[str, Any]]] = [
        ("put", f"/questions/{qid}", {"json": edit}),
        ("put", f"/questions/{qid}/rubric", {"json": {"criteria": []}}),
        ("post", f"/questions/{qid}/reference-answers", {"json": {"text": "mine"}}),
        ("put", f"/questions/{qid}/glossary", {"json": {"terms": ["x"]}}),
        (
            "post",
            f"/questions/{qid}/key-files?filename=k.pdf&confirm_no_student_data=true",
            {"content": PDF, "headers": {**b, "Content-Type": "application/pdf"}},
        ),
        (
            "post",
            f"/questions/{qid}/diagrams?filename=d.png&confirm_no_student_data=true",
            {"content": PNG, "headers": {**b, "Content-Type": "image/png"}},
        ),
    ]
    for method, path, kw in attempts:
        kw.setdefault("headers", b)
        response = client.request(method, BASE + path, **kw)
        assert response.status_code == 403, (path, response.text)
        assert "copy it first" in response.json()["detail"]
    assert client.get(f"{BASE}/questions/{qid}", headers=a).json()["text"] == q["text"]

    copied = client.post(f"{BASE}/questions/{qid}/copy", headers=b)
    assert copied.status_code == 201, copied.text
    mine = copied.json()
    assert mine["id"] != qid and mine["owned"] is True and mine["version"] == 1
    assert mine["copied_from"]["id"] == qid and mine["code"] == q["code"]
    assert [x["text"] for x in mine["reference_answers"]] == ["Model."]
    assert mine["rubric"]["complete"] is True
    assert [(f["name"], f["keywords"]) for f in mine["key_files"]] == [("key.pdf", ["lens"])]
    # B edits its own copy; A's stays as it was.
    own = client.put(
        f"{BASE}/questions/{mine['id']}", json={**edit, "text": "B's wording"}, headers=b
    )
    assert own.status_code == 200 and own.json()["text"] == "B's wording"
    assert client.get(f"{BASE}/questions/{qid}", headers=a).json()["text"] == q["text"]
    assert client.put(f"{BASE}/questions/{mine['id']}", json=edit, headers=a).status_code == 403


def test_codes_are_unique_per_college(
    client: TestClient, a: dict[str, str], b: dict[str, str], subject: str
) -> None:
    make(client, a, subject, code="PHY-Q1")
    dup = client.post(f"{BASE}/questions", json=body(subject, code="phy-q1"), headers=a)
    assert dup.status_code == 409 and "already has a question with code" in dup.json()["detail"]
    other = client.post(f"{BASE}/questions", json=body(subject, code="PHY-Q1"), headers=b)
    assert other.status_code == 201  # another college may use the same code
    renamed = client.put(
        f"{BASE}/questions/{other.json()['id']}",
        json={
            "code": "PHY-Q9",
            "text": "t",
            "max_marks": 4,
            "difficulty": "medium",
            "category": "",
        },
        headers=b,
    )
    assert renamed.status_code == 200 and renamed.json()["code"] == "PHY-Q9"


def test_reference_answers_and_glossary(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject)
    qid = q["id"]
    first = client.post(
        f"{BASE}/questions/{qid}/reference-answers", json={"text": "One"}, headers=a
    )
    guide = client.post(
        f"{BASE}/questions/{qid}/reference-answers",
        json={"text": "Mark by judgement", "guidance_only": True},
        headers=a,
    )
    assert first.status_code == guide.status_code == 201 and guide.json()["guidance_only"] is True
    edited = client.put(
        f"{BASE}/questions/{qid}/reference-answers/{first.json()['id']}",
        json={"text": "One, improved"},
        headers=a,
    )
    assert edited.json()["version"] == 2
    gone = client.delete(
        f"{BASE}/questions/{qid}/reference-answers/{guide.json()['id']}", headers=a
    )
    assert gone.status_code == 204
    assert (
        client.delete(
            f"{BASE}/questions/{qid}/reference-answers/{guide.json()['id']}", headers=a
        ).status_code
        == 404
    )
    got = client.get(f"{BASE}/questions/{qid}", headers=a).json()
    assert [x["text"] for x in got["reference_answers"]] == ["One, improved"] and got[
        "key_count"
    ] == 1

    glossary = client.put(
        f"{BASE}/questions/{qid}/glossary",
        json={"terms": ["Focal length", " focal   length ", "lens"]},
        headers=a,
    )
    assert glossary.json() == {
        "teacher_terms": ["Focal length", "lens"],
        "reference_labels": [],
        "terms": ["Focal length", "lens"],
    }
    assert client.get(f"{BASE}/questions/{qid}", headers=a).json()["glossary"] == glossary.json()


# --- uploads ----------------------------------------------------------------------------------


def test_pdf_and_image_keys_upload_and_download(
    client: TestClient, a: dict[str, str], b: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject)
    for name, data, ctype in (
        ("k.pdf", PDF, "application/pdf"),
        ("k.png", PNG, "image/png"),
        ("k.jpg", JPEG, "image/jpeg"),
    ):
        response = upload(client, a, q["id"], data, ctype, filename=name, keywords=["a", "b"])
        assert response.status_code == 201, response.text
        out = response.json()
        assert (out["name"], out["media_type"], out["size_bytes"]) == (name, ctype, len(data))
        assert out["keywords"] == ["a", "b"]
        for who in (a, b):  # global content: every college may read it
            got = client.get(out["content_url"], headers=who)
            assert got.status_code == 200 and got.content == data
            assert got.headers["content-type"].startswith(ctype)
            assert got.headers["x-content-type-options"] == "nosniff"
            assert name in got.headers["content-disposition"]
    detail = client.get(f"{BASE}/questions/{q['id']}", headers=a).json()
    assert [f["name"] for f in detail["key_files"]] == ["k.pdf", "k.png", "k.jpg"]
    assert detail["key_count"] == 3
    assert (
        client.get(f"{BASE}/questions/{q['id']}/key-files/{q['id']}/content", headers=a).status_code
        == 404
    )


def test_upload_types_are_read_from_the_bytes(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject)
    cases = [
        (b"just text", "application/pdf", "Only PDF, PNG or JPEG"),
        (
            b"PK\x03\x04",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Only PDF, PNG or JPEG",
        ),
        (PDF, "image/png", "says it is image/png but its content is application/pdf"),
        (b"", "application/pdf", "empty"),
    ]
    for data, ctype, message in cases:
        response = upload(client, a, q["id"], data, ctype)
        assert response.status_code == 422, (ctype, response.text)
        assert message in response.json()["detail"]
    assert upload(client, a, q["id"], PDF, "application/octet-stream").status_code == 201


def test_the_no_student_data_confirmation_is_required(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject)
    for confirm in ("false", None):
        params: dict[str, Any] = {} if confirm is None else {"confirm_no_student_data": confirm}
        response = client.post(
            f"{BASE}/questions/{q['id']}/key-files",
            params={"filename": "k.pdf", **params},
            content=PDF,
            headers={**a, "Content-Type": "application/pdf"},
        )
        assert response.status_code == 422 and "no student data" in response.json()["detail"]
        diagram = client.post(
            f"{BASE}/questions/{q['id']}/diagrams",
            params={"filename": "d.png", **params},
            content=PNG,
            headers={**a, "Content-Type": "image/png"},
        )
        assert diagram.status_code == 422
    assert client.get(f"{BASE}/questions/{q['id']}", headers=a).json()["key_files"] == []


def test_reference_diagrams_are_png_only_and_downloadable(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject)
    url = f"{BASE}/questions/{q['id']}/diagrams"
    for data, ctype in ((PDF, "application/pdf"), (JPEG, "image/jpeg")):
        response = client.post(
            url,
            params={"filename": "d", "confirm_no_student_data": "true"},
            content=data,
            headers={**a, "Content-Type": ctype},
        )
        assert response.status_code == 422 and "Only PNG" in response.json()["detail"]
    ok = client.post(
        url,
        params={"filename": "My flow chart.png", "confirm_no_student_data": "true"},
        content=PNG,
        headers={**a, "Content-Type": "image/png"},
    )
    assert ok.status_code == 201
    out = ok.json()
    assert out["name"] == "My_flow_chart.png" and out["node_count"] == 0
    assert client.get(out["content_url"], headers=a).content == PNG


def test_huge_uploads_are_refused_before_reading(
    client: TestClient, a: dict[str, str], subject: str
) -> None:
    q = make(client, a, subject)
    big = PDF + b"0" * (10 * 1024 * 1024 + 2048)
    response = upload(client, a, q["id"], big, "application/pdf")
    assert response.status_code == 413


# --- search -----------------------------------------------------------------------------------


def test_filters_pagination_and_topics(
    client: TestClient, a: dict[str, str], b: dict[str, str], subject: str
) -> None:
    maths = client.post(f"{BASE}/subjects", json={"code": "MAT", "name": "Maths"}, headers=a).json()
    make(
        client,
        a,
        subject,
        code="PHY-Q1",
        text="State Lenz's law.",
        category="Electromagnetism",
        difficulty="easy",
        reference_answer="Induced emf opposes the change.",
    )
    make(
        client,
        a,
        subject,
        code="PHY-Q2",
        text="Define resonance.",
        category="AC Circuits",
        difficulty="hard",
    )
    make(
        client,
        a,
        maths["id"],
        code="MAT-Q1",
        text="Integrate x dx.",
        category="Calculus",
        difficulty="easy",
    )
    make(client, b, subject, code="PHY-B1", text="B's question.", category="Optics")

    def codes(**params: Any) -> list[str]:
        response = client.get(f"{BASE}/questions", params=params, headers=a)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == len(page["items"])
        return [i["code"] for i in page["items"]]

    assert codes() == ["MAT-Q1", "PHY-B1", "PHY-Q1", "PHY-Q2"]
    assert codes(subject_id=subject) == ["PHY-B1", "PHY-Q1", "PHY-Q2"]
    assert codes(difficulty="easy") == ["MAT-Q1", "PHY-Q1"]
    assert codes(topic="ac circuits") == ["PHY-Q2"]
    assert codes(code="q1") == ["MAT-Q1", "PHY-Q1"]
    assert codes(keyword="lenz") == ["PHY-Q1"]
    assert codes(keyword="opposes") == ["PHY-Q1"]
    assert codes(mine="true") == ["MAT-Q1", "PHY-Q1", "PHY-Q2"]
    assert codes(keyword="lenz", difficulty="hard") == []
    assert (
        client.get(f"{BASE}/questions", params={"difficulty": "extreme"}, headers=a).status_code
        == 422
    )
    page = client.get(f"{BASE}/questions", params={"limit": 2, "offset": 1}, headers=a).json()
    assert (page["total"], len(page["items"]), page["limit"], page["offset"]) == (4, 2, 2, 1)
    first = page["items"][0]
    assert first["owner_name"] and first["subject_name"] == "Physics" and first["key_count"] == 0
    assert any(
        i["key_count"] == 1 for i in client.get(f"{BASE}/questions", headers=a).json()["items"]
    )
    assert client.get(f"{BASE}/question-topics", headers=a).json() == [
        "AC Circuits",
        "Calculus",
        "Electromagnetism",
        "Optics",
    ]
    assert client.get(
        f"{BASE}/question-topics", params={"subject_id": maths["id"]}, headers=a
    ).json() == ["Calculus"]


def test_sign_in_is_required_and_unknown_ids_are_404(client: TestClient, a: dict[str, str]) -> None:
    for method, path in (
        ("get", "/questions"),
        ("post", "/questions"),
        ("get", "/question-topics"),
    ):
        assert client.request(method, BASE + path).status_code == 401
    missing = "00000000-0000-4000-8000-0000000000aa"
    assert client.get(f"{BASE}/questions/{missing}", headers=a).status_code == 404
    assert client.post(f"{BASE}/questions/{missing}/copy", headers=a).status_code == 404
    assert client.get(f"{BASE}/questions/not-a-uuid", headers=a).status_code == 422
