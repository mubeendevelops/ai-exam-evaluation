"""Diagram graphs over HTTP (P14): upload queues recognition, the reference graph is read and
edited by the owning college (new versions), a booklet's drawings are listed and corrected
(the answer goes back to the worker to be re-scored), and the R6 documents of a score are
served. In-memory backends; the worker's steps run here with a scripted recognizer."""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from tarn_api.testing import MemoryBackends
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.diagram import DiagramKind
from tarn_core.domain.tenancy import Role, Student
from tarn_core.ids import CollegeId, StudentId
from tarn_core.ports.jobs import JOB_RECOGNIZE_REFERENCE, JOB_RESCORE_ANSWERS
from tarn_core.services.diagrams.scorer import DiagramScorer
from tarn_core.services.diagrams.service import BookletDiagrams, ReferenceDiagrams
from tarn_core.services.scoring import ScoringService
from tarn_core.testing import ScriptedDiagramRecognizer
from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld
from tarn_core.testing.builders import CollegeFixture, make_services
from tarn_core.testing.diagrams import PNG, DiagramWorld, diagram_world, flowchart_detection

BASE = "/api/v1"
COMPARISON_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "docs/api/diagram-comparison.schema.json").read_text()
)


def _login(client: TestClient, institution: str, email: str, password: str) -> dict[str, str]:
    response = client.post(
        f"{BASE}/auth/login",
        json={"institution_id": institution, "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@dataclass
class Setup:
    client: TestClient
    mem: Any
    teacher: dict[str, str]
    other: dict[str, str]
    college_id: CollegeId
    dw: DiagramWorld[Any]

    def recognise_reference(self) -> None:
        """What the worker does for ``diagram.reference``."""
        ReferenceDiagrams(
            content=self.mem.content,
            blobs=self.mem.blobs,
            runtime=self.mem.runtime,
            recognizers={DiagramKind.FLOWCHART: ScriptedDiagramRecognizer(flowchart_detection())},
            labels=None,
            glossary=make_services(self.mem).bank,
        ).recognize(self.college_id, self.dw.college.teacher.id, self.dw.reference.id)

    def recognise_booklet(self, reverse: bool = False) -> UUID:
        answer = self.dw.answer()
        b = self.mem.booklets.get(self.college_id, self.dw.booklet.id)
        self.mem.booklets.save(
            self.college_id, replace(b, status=BookletStatus.SEGMENTED, version=b.version + 1)
        )
        stage = BookletDiagrams(
            booklets=self.mem.booklets,
            content=self.mem.content,
            blobs=self.mem.blobs,
            runtime=self.mem.runtime,
            jobs=self.mem.jobs,
            recognizers={
                DiagramKind.FLOWCHART: ScriptedDiagramRecognizer(
                    flowchart_detection(reverse_second=reverse)
                )
            },
        )
        while not stage.step(self.college_id, self.dw.booklet.id):
            pass
        ScoringService.standard(
            booklets=self.mem.booklets,
            scores=self.mem.scores,
            content=self.mem.content,
            runtime=self.mem.runtime,
            embedder=None,
            extra=[DiagramScorer()],
        ).score_answer(self.college_id, None, answer.id)
        return answer.id

    @property
    def graph_url(self) -> str:
        return f"{BASE}/questions/{self.dw.question.id}/diagrams/{self.dw.reference.id}/graph"


@pytest.fixture
def s(client: TestClient, backends: MemoryBackends, world: AuthWorld) -> Setup:
    mem = backends.mem
    cid, admin = world.register("COLLEGE_A", "admin@a.example")
    world.invite(cid, admin, "teacher@a.example")
    world.register("COLLEGE_B", "admin@b.example")
    users = list(mem.users.list(cid))
    student = Student(id=StudentId(mem.ids.new()), college_id=cid, name="S One", usn="TST001")
    mem.students.save(cid, student)
    fixture = CollegeFixture(
        college=mem.colleges.get(cid),
        teacher=next(u for u in users if u.role is Role.TEACHER),
        admin=next(u for u in users if u.role is Role.ADMIN),
        students=(student,),
    )
    return Setup(
        client=client,
        mem=mem,
        teacher=_login(client, "COLLEGE_A", "teacher@a.example", TEACHER_PASSWORD),
        other=_login(client, "COLLEGE_B", "admin@b.example", ADMIN_PASSWORD),
        college_id=cid,
        dw=diagram_world(mem, college=fixture),
    )


def test_upload_queues_recognition_and_shows_pending(s: Setup) -> None:
    response = s.client.post(
        f"{BASE}/questions/{s.dw.question.id}/diagrams"
        "?filename=tree.png&confirm_no_student_data=true&kind=tree",
        content=PNG,
        headers={**s.teacher, "Content-Type": "image/png"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "tree" and body["recognition"] == "pending"
    assert body["graph_url"].endswith(f"/diagrams/{body['id']}/graph")
    jobs = [j for j in s.mem.jobs.jobs if j.kind == JOB_RECOGNIZE_REFERENCE]
    assert any(j.payload["reference_diagram_id"] == body["id"] for j in jobs)


def test_reference_graph_read_edit_and_conflict(s: Setup) -> None:
    pending = s.client.get(s.graph_url, headers=s.teacher).json()
    assert pending["recognition"] == "pending" and pending["graph"]["nodes"] == []
    s.recognise_reference()
    current = s.client.get(s.graph_url, headers=s.teacher).json()
    assert current["recognition"] == "recognised"
    assert [n["shape"] for n in current["graph"]["nodes"]] == ["terminal", "io", "terminal"]
    edit = {
        "expected_version": current["version"],
        "edits": [
            {"op": "relabel_node", "id": "n1", "label": "start"},
            {"op": "relabel_node", "id": "n2", "label": "read n"},
            {"op": "relabel_node", "id": "n3", "label": "stop"},
            {"op": "add_edge", "source": "n1", "target": "n3", "label": "skip"},
        ],
        "kind": "flowchart",
    }
    done = s.client.post(f"{s.graph_url}/edits", json=edit, headers=s.teacher)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["version"] == current["version"] + 1 and body["recognition"] == "edited"
    assert body["graph"]["edited_by_teacher"] is True
    assert len(body["graph"]["edges"]) == 3
    stale = s.client.post(f"{s.graph_url}/edits", json=edit, headers=s.teacher)
    assert stale.status_code == 409
    bad = {"expected_version": body["version"], "edits": [{"op": "remove_node", "id": "zz"}]}
    assert s.client.post(f"{s.graph_url}/edits", json=bad, headers=s.teacher).status_code == 422
    unknown = {"expected_version": body["version"], "edits": [{"op": "explode"}]}
    assert s.client.post(f"{s.graph_url}/edits", json=unknown, headers=s.teacher).status_code == 422


def test_another_college_reads_but_cannot_edit_or_rerun(s: Setup) -> None:
    s.recognise_reference()
    current = s.client.get(s.graph_url, headers=s.other)
    assert current.status_code == 200  # global content: readable, copy to edit
    edit = {
        "expected_version": current.json()["version"],
        "edits": [{"op": "remove_node", "id": "n1"}],
    }
    assert s.client.post(f"{s.graph_url}/edits", json=edit, headers=s.other).status_code == 403
    rerun = s.graph_url.replace("/graph", "/recognize")
    assert s.client.post(rerun, headers=s.other).status_code == 403
    assert s.client.post(rerun, headers=s.teacher).status_code == 202


def test_booklet_drawings_edit_and_comparisons(s: Setup) -> None:
    s.recognise_reference()
    answer_id = s.recognise_booklet(reverse=True)
    url = f"{BASE}/booklets/{s.dw.booklet.id}/diagrams"
    (drawing,) = s.client.get(url, headers=s.teacher).json()
    assert drawing["version"] == 1 and drawing["kind"] == "flowchart"
    assert [e["source"] for e in drawing["graph"]["edges"]] == ["n1", "n3"]

    comparisons_url = f"{BASE}/booklets/{s.dw.booklet.id}/answers/{answer_id}/diagram-comparisons"
    (comparison,) = s.client.get(comparisons_url, headers=s.teacher).json()
    assert [e["status"] for e in comparison["document"]["edges"]] == ["present", "reversed"]
    assert Draft202012Validator(COMPARISON_SCHEMA).is_valid(comparison["document"])

    edit = {"expected_version": 1, "edits": [{"op": "reverse_edge", "id": "e2"}]}
    closed = s.client.post(f"{url}/{drawing['id']}/edits", json=edit, headers=s.teacher)
    assert closed.status_code == 423  # open the booklet first (P15)
    lock = s.client.post(f"{BASE}/booklets/{s.dw.booklet.id}/lock", headers=s.teacher)
    assert lock.status_code == 200, lock.text
    done = s.client.post(f"{url}/{drawing['id']}/edits", json=edit, headers=s.teacher)
    assert done.status_code == 200, done.text
    assert done.json()["version"] == 2
    rescore = [j for j in s.mem.jobs.jobs if j.kind == JOB_RESCORE_ANSWERS]
    assert [j.payload["answer_ids"] for j in rescore] == [[str(answer_id)]]

    # another college sees nothing
    assert s.client.get(url, headers=s.other).status_code == 404
    assert s.client.get(comparisons_url, headers=s.other).status_code == 404
    other_edit = s.client.post(f"{url}/{drawing['id']}/edits", json=edit, headers=s.other)
    assert other_edit.status_code == 404
