"""R1 and R2 in one test: the same booklet through the API path (HTTP upload, then the worker's
stages) and the CLI path (``tarn evaluate``: a folder, the same stages) gets the same marks,
criterion credits, flags and totals. Both run on the in-memory adapters with scripted
engines, because the proof is that the core does not care where pages or requests come from."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pymupdf
import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.local import build_evaluation
from tarn_adapters.sources import FolderPageSource
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import scripted_stages
from tarn_api.app import create_app
from tarn_api.testing import MemoryBackends
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.ids import BookletId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing import (
    InMemory,
    fake_pdf,
)
from tarn_core.testing.seed_world import SeededCollege, seed_in_memory

BASE = "/api/v1"
TEACHER = "suresh.naik@demo-com.example.test"


def commerce(mem: InMemory, seeded: list[SeededCollege]) -> SeededCollege:
    return next(s for s in seeded if s.seed.institution_id == "DEMO_COM")


def qp_ci(mem: InMemory) -> ExamBlueprint:
    return next(b for b in mem.content.latest(ExamBlueprint) if b.title.startswith("QP-CI"))


def cli_result(tmp_path: Path) -> tuple[dict[str, Any], bytes]:
    mem = InMemory()
    college = commerce(mem, seed_in_memory(mem))
    folder = tmp_path / "booklet"
    folder.mkdir()
    (folder / "pages.pdf").write_bytes(fake_pdf(1))
    evaluation = build_evaluation(
        mem,
        scripted_stages(mem.blobs),
        FolderPageSource(folder),
    )
    done = evaluation.evaluate(
        college.accounts.college_id,
        college.accounts.teacher_ids[0],
        student_id=mem.students.list(college.accounts.college_id)[0].id,
        blueprint_id=qp_ci(mem).id,
    )
    assert done.sheet_pdf is not None
    return json.loads(json.dumps(done.result.as_json())), done.sheet_pdf


def api_review(client: TestClient, backends: MemoryBackends) -> dict[str, Any]:
    mem = backends.mem
    college = commerce(mem, seed_in_memory(mem))
    login = client.post(
        f"{BASE}/auth/login",
        json={"institution_id": "DEMO_COM", "email": TEACHER, "password": DEV_PASSWORD},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    students = client.get(f"{BASE}/students", headers=headers).json()
    upload = client.post(
        f"{BASE}/booklets",
        headers=headers,
        data={"student_id": students[0]["id"], "blueprint_id": str(qp_ci(mem).id)},
        files=[("files", ("pages.pdf", fake_pdf(1), "application/pdf"))],
    )
    assert upload.status_code in (200, 201, 202), upload.text
    booklet_id = BookletId(UUID(upload.json()["id"]))
    # What the worker does with the queued job: the same stages, on the same in-memory queue.
    errors = LocalStageRunner(scripted_stages(mem.blobs), mem).run(
        college.accounts.college_id, booklet_id
    )
    assert errors == []
    review = client.get(f"{BASE}/booklets/{booklet_id}/review", headers=headers)
    assert review.status_code == 200, review.text
    body: dict[str, Any] = review.json()
    return body


def number(value: str | None) -> float | None:
    return None if value is None else float(value)


def from_cli(result: dict[str, Any]) -> dict[str, Any]:
    """The comparable part of the CLI's result.json."""
    return {
        "total": number(result["total"]),
        "max_marks": number(result["max_marks"]),
        "slots": [
            (s["section"], s["slot"], number(s["mark"]), s["counted"], s["outcome"])
            for s in result["slots"]
        ],
        "answers": {
            a["label"]: {
                "mark": number(a["mark"]),
                "flags": a["flags"],
                "reasons": a["reasons"],
                "relevance": a["relevance"],
                "criteria": [
                    (number(c["credit"]), number(c["marks"]), c["scorer"], c["flags"])
                    for c in a["criteria"]
                ],
            }
            for a in result["answers"]
        },
    }


def from_api(review: dict[str, Any]) -> dict[str, Any]:
    """The same, from the API's review of the booklet."""
    totals = review["totals"]
    return {
        "total": totals["total"],
        "max_marks": totals["max_marks"],
        "slots": [
            (s["section_label"], s["slot_label"], s["mark"], s["counted"], s["outcome"])
            for s in totals["slots"]
        ],
        "answers": {
            a["slot_label"]: {
                "mark": a["suggestion"]["mark"],
                "flags": a["suggestion"]["flags"],
                "reasons": a["suggestion"]["reasons"],
                "relevance": a["suggestion"]["relevance"],
                "criteria": [
                    (c["credit"], c["marks"], c["scorer"], c["flags"])
                    for c in a["suggestion"]["criteria"]
                ],
            }
            for a in review["answers"]
            if a["suggestion"] is not None
        },
    }


def test_the_api_path_and_the_cli_path_give_identical_scores(tmp_path: Path) -> None:
    result, _ = cli_result(tmp_path)
    backends = MemoryBackends()
    app = create_app(Settings(_env_file=None, device="cpu"), backends=backends)
    with TestClient(app) as client:
        review = api_review(client, backends)

    cli, api = from_cli(result), from_api(review)
    assert set(cli["answers"]) == {"8", "9", "10"}  # a real segmentation, not an empty one
    assert any(a["criteria"] for a in cli["answers"].values())
    assert cli["total"] == pytest.approx(api["total"]) and cli["total"] > 0
    assert cli == api


def test_the_cli_sheet_is_a_draft_and_nothing_is_approved(tmp_path: Path) -> None:
    result, pdf = cli_result(tmp_path)
    assert result["approved"] is False and result["status"] == "scored"
    opened: Any = pymupdf.open  # PyMuPDF ships no types for the document API
    text = "".join(page.get_text() for page in opened("pdf", pdf))
    assert "DRAFT" in text and "No teacher has approved them" in text.replace("\n", " ")
    assert "AI-suggested total" in text
    assert "result sheet v" not in text
    assert "Evaluated by" not in text  # no teacher evaluated it
