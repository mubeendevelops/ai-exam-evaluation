"""Manage Question Papers over HTTP (P34): the list with its filters and paging, create, clone,
the structure lock after an upload (D222), and the paper PDF (D224). In-memory backends with
the demo seed (synthetic data)."""

from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient

from tarn_api.testing import MemoryBackends, queued_booklet
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import fake_pdf
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
ADMIN = "admin@demo-com.example.test"
SURESH = "suresh.naik@demo-com.example.test"
RAVI = "ravi.menon@demo-eng.example.test"
QP_CI = "QP-CI-2021-06"
QP_IPR = "QP-IPR-2021-07"


class Flow:
    def __init__(self, client: TestClient, backends: MemoryBackends) -> None:
        self.client = client
        self.mem = backends.mem
        self.admin = self.login("DEMO_COM", ADMIN)
        self.suresh = self.login("DEMO_COM", SURESH)
        self.ravi = self.login("DEMO_ENG", RAVI)

    def login(self, institution: str, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": institution, "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def papers(self, who: dict[str, str] | None = None, **params: Any) -> dict[str, Any]:
        response = self.client.get(f"{BASE}/papers", params=params, headers=who or self.suresh)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def paper(self, exam_code: str) -> dict[str, Any]:
        for p in self.client.get(f"{BASE}/blueprints", headers=self.suresh).json():
            if p["exam_code"] == exam_code:
                full: dict[str, Any] = self.client.get(
                    f"{BASE}/blueprints/{p['id']}", headers=self.suresh
                ).json()
                return full
        raise KeyError(exam_code)

    def upload_to(self, exam_code: str) -> None:
        unit = next(
            u
            for u in self.client.get(f"{BASE}/units", headers=self.admin).json()
            if u["paper"]["exam_code"] == exam_code
        )
        usn = min(s["usn"] for s in self.client.get(f"{BASE}/students", headers=self.admin).json())
        response = self.client.post(
            f"{BASE}/units/{unit['id']}/booklets",
            headers=self.admin,
            files=[("files", (f"{usn}.pdf", fake_pdf(1, usn), "application/pdf"))],
        )
        queued_booklet(response)


@pytest.fixture
def flow(client: TestClient, backends: MemoryBackends) -> Flow:
    seed_in_memory(backends.mem)
    return Flow(client, backends)


def test_the_list_pages_filters_and_offers_descriptors(flow: Flow) -> None:
    listing = flow.papers()
    titles = [p["title"] for p in listing["items"]]
    assert listing["total"] == len(titles) == 4  # the seed grants both SMEs every subject
    ci = next(p for p in listing["items"] if p["exam_code"] == QP_CI)
    assert ci["in_use"] is True and ci["subject_name"] and ci["total_marks"] > 0
    assert ci["section_count"] >= 1 and "created_at" in ci and "updated_at" in ci
    assert listing["descriptors"] == []

    flow.mem.clock.advance(minutes=5)
    made = flow.client.post(
        f"{BASE}/papers",
        json={"title": "Mid term 2026", "subject_id": ci["subject_id"], "descriptor": "Mid-term"},
        headers=flow.suresh,
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["kind"] == "paper" and body["descriptor"] == "Mid-term"
    assert body["unlinked_count"] == 5 and body["structure_locked"] is False

    after = flow.papers()
    assert after["total"] == 5 and after["descriptors"] == ["Mid-term"]
    assert after["items"][0]["title"] == "Mid term 2026"  # most recently saved first
    assert after["items"][0]["in_use"] is False
    paged = flow.papers(limit=2, offset=4)
    assert (paged["total"], len(paged["items"]), paged["offset"]) == (5, 1, 4)
    assert [p["title"] for p in flow.papers(q="MID-T")["items"]] == ["Mid term 2026"]
    assert [p["title"] for p in flow.papers(descriptor="mid-term")["items"]] == ["Mid term 2026"]
    assert flow.papers(descriptor="mid")["total"] == 0
    today = after["items"][0]["created_at"][:10]
    assert "Mid term 2026" in [p["title"] for p in flow.papers(created_on=today)["items"]]
    assert flow.papers(created_on="2001-01-01")["total"] == 0
    bad = flow.client.get(f"{BASE}/papers", params={"limit": 0}, headers=flow.suresh)
    assert bad.status_code == 422


def test_create_from_a_schema_and_clone_with_a_descriptor(flow: Flow) -> None:
    ci = flow.paper(QP_CI)
    schema = flow.client.post(
        f"{BASE}/blueprints",
        json={
            "schema_version": "1.2",
            "kind": "schema",
            "title": "Constitution matrix",
            "course_code": "CI",
            "subject_id": ci["subject_id"],
            "total_marks": 4,
            "sections": [
                {
                    "label": "A",
                    "method": "semantic_rubric",
                    "items": [
                        {"type": "question", "label": "1", "marks": 2},
                        {"type": "question", "label": "2", "marks": 2},
                    ],
                }
            ],
        },
        headers=flow.suresh,
    )
    assert schema.status_code == 201, schema.text
    made = flow.client.post(
        f"{BASE}/papers",
        json={
            "title": "From the matrix",
            "subject_id": ci["subject_id"],
            "schema_id": schema.json()["id"],
        },
        headers=flow.suresh,
    )
    assert made.status_code == 201, made.text
    assert made.json()["from_schema"] == schema.json()["id"]
    copy = flow.client.post(
        f"{BASE}/blueprints/{ci['id']}/clone",
        json={"title": "QP-CI again", "descriptor": "Board Final"},
        headers=flow.suresh,
    )
    assert copy.status_code == 201, copy.text
    assert copy.json()["descriptor"] == "Board Final" and copy.json()["exam_code"] == ""


def test_the_structure_locks_once_a_booklet_is_uploaded(flow: Flow) -> None:
    paper = flow.paper(QP_CI)
    assert paper["structure_locked"] is False
    url = f"{BASE}/blueprints/{paper['id']}"
    document = paper["document"]
    flow.upload_to(QP_CI)
    locked = flow.client.get(url, headers=flow.suresh).json()
    assert locked["structure_locked"] is True

    retitled = {**document, "title": "Constitution, June 2021", "descriptor": "Board Final"}
    saved = flow.client.put(url, json=retitled, headers=flow.suresh)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == paper["version"] + 1

    sections = [dict(s) for s in document["sections"]]
    sections[0] = {**sections[0], "title": "Something else"}
    refused = flow.client.put(url, json={**document, "sections": sections}, headers=flow.suresh)
    assert refused.status_code == 409
    assert "clone it to change the structure" in refused.json()["detail"]
    code = flow.client.put(url, json={**document, "exam_code": "QP-CI-NEW"}, headers=flow.suresh)
    assert code.status_code == 409


def test_the_paper_pdf(flow: Flow) -> None:
    paper = flow.paper(QP_CI)
    url = f"{BASE}/blueprints/{paper['id']}/versions/{paper['version']}/paper.pdf"
    response = flow.client.get(url, headers=flow.suresh)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        f'inline; filename="paper-{QP_CI}-v{paper["version"]}.pdf"'
    )
    assert response.headers["cache-control"] == "private, no-cache"
    opened: Any = pymupdf.open  # PyMuPDF ships no types for the document API
    doc = opened("pdf", response.content)
    text = " ".join(page.get_text() for page in doc)
    assert paper["title"].split(",")[0] in text and QP_CI in text
    assert (
        flow.client.get(
            url.replace("/versions/1/", "/versions/99/"), headers=flow.suresh
        ).status_code
        == 404
    )


def test_another_subject_or_college_is_not_found_and_the_admin_refused(flow: Flow) -> None:
    anita = flow.login("DEMO_COM", "anita.desai@demo-com.example.test")
    subject = flow.client.post(
        f"{BASE}/subjects", json={"code": "ECO", "name": "Economics"}, headers=flow.admin
    ).json()
    anita_id = next(
        a["user"]["id"]
        for a in flow.client.get(f"{BASE}/accounts", headers=flow.admin).json()
        if a["user"]["email"].startswith("anita.")
    )
    granted = flow.client.post(
        f"{BASE}/accounts/{anita_id}/subjects/{subject['id']}", headers=flow.admin
    )
    assert granted.status_code == 200, granted.text
    made = flow.client.post(
        f"{BASE}/papers",
        json={"title": "Economics mid term", "subject_id": subject["id"], "descriptor": "Eco"},
        headers=anita,
    ).json()
    pdf = f"{BASE}/blueprints/{made['id']}/versions/1/paper.pdf"
    assert flow.client.get(pdf, headers=anita).status_code == 200
    assert flow.client.get(pdf, headers=flow.suresh).status_code == 404  # not his subject
    assert flow.client.get(pdf, headers=flow.ravi).status_code == 404  # another college
    assert flow.client.get(pdf, headers=flow.admin).status_code == 403
    mine = flow.papers()
    assert "Economics mid term" not in [p["title"] for p in mine["items"]]
    assert "Eco" not in mine["descriptors"]
    assert flow.papers(anita, q="economics")["total"] == 1
    assert flow.client.get(f"{BASE}/papers", headers=flow.admin).status_code == 403
    refused = flow.client.post(
        f"{BASE}/papers",
        json={"title": "Not mine", "subject_id": subject["id"]},
        headers=flow.suresh,
    )
    assert refused.status_code == 404
    foreign = flow.client.post(
        f"{BASE}/papers",
        json={"title": "Elsewhere", "subject_id": subject["id"]},
        headers=flow.ravi,
    )
    assert foreign.status_code == 422  # another college's subject is "no such subject"
    assert all(p["title"] != "Elsewhere" for p in flow.papers(flow.ravi)["items"])
