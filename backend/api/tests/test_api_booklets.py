"""Booklet upload into a unit (P26), page-cleaning status, the admin's retake decisions and
the SME's views over HTTP. In-memory backends; the image work is faked (tiny byte strings), the
pipeline is the real core one."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_api.testing import MemoryBackends
from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.domain.tenancy import SubjectGrant
from tarn_core.errors import EngineTimeoutError
from tarn_core.ids import BlueprintId, BookletId, CollegeId
from tarn_core.ports.engines import DetectedRegion, OcrEngine, TableCell
from tarn_core.services.ocr.reader import BookletReader, OrientationPolicy
from tarn_core.services.pipeline import PagePipeline
from tarn_core.services.segmentation.service import BookletSegmenter
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.services.uploads import UploadLimits
from tarn_core.testing import (
    FakePageCleaner,
    FakePageSplitter,
    FakePageTransform,
    ScriptedLayoutDetector,
    ScriptedOcrEngine,
    fake_image,
    fake_pdf,
)
from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld
from tarn_core.testing.builders import CollegeFixture, ci_shaped_blueprint, unit_for

BASE = "/api/v1"
ROSTER = "name,usn,class/section\nAsha Test,TST001,A\nBala Test,TST002,A\n"
USNS = ("TST001", "TST002")


def login(client: TestClient, institution: str, email: str, password: str) -> dict[str, str]:
    response = client.post(
        f"{BASE}/auth/login",
        json={"institution_id": institution, "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@dataclass
class Setup:
    client: TestClient
    backends: MemoryBackends
    college_id: CollegeId
    teacher: dict[str, str]
    admin: dict[str, str]
    students: list[str]
    blueprint_id: str
    unit_id: str

    def send(
        self, *files: tuple[str, bytes], headers: dict[str, str] | None = None, unit: str = ""
    ) -> Any:
        """``POST /units/{id}/booklets`` with these (name, bytes) files."""
        return self.client.post(
            f"{BASE}/units/{unit or self.unit_id}/booklets",
            headers=headers or self.admin,  # uploading is the admin's job (D162)
            files=[("files", (name, data, "application/pdf")) for name, data in files],
        )

    def upload(self, pdf: bytes, *, student: int = 0) -> dict[str, Any]:
        """One student's PDF, queued: the booklet as the SME sees it."""
        response = self.send((f"{USNS[student]}.pdf", pdf))
        assert response.status_code == 200, response.text
        (result,) = response.json()["files"]
        assert result["outcome"] == "queued", result
        booklet = self.client.get(f"{BASE}/booklets/{result['booklet_id']}", headers=self.teacher)
        assert booklet.status_code == 200, booklet.text
        body: dict[str, Any] = booklet.json()
        return body

    def process(self, booklet_id: str) -> None:
        """What the worker does: step the pipeline until the booklet is finished."""
        mem = self.backends.mem
        pipeline = PagePipeline(
            booklets=mem.booklets,
            blobs=mem.blobs,
            splitter=FakePageSplitter(),
            cleaner=FakePageCleaner(),
            runtime=mem.runtime,
            jobs=mem.jobs,
        )
        while not pipeline.step(self.college_id, BookletId(UUID(booklet_id))):
            pass

    def read(self, booklet_id: str, engines: dict[str, OcrEngine] | None = None) -> None:
        """What the worker does next: read the clean pages (scripted layout and engines)."""
        mem = self.backends.mem
        reader = BookletReader(
            booklets=mem.booklets,
            content=mem.content,
            blobs=mem.blobs,
            layout=ScriptedLayoutDetector(
                [
                    DetectedRegion(kind=RegionKind.TEXT_LINE, box=Box(x0=10, y0=10, x1=500, y1=60)),
                    DetectedRegion(
                        kind=RegionKind.TABLE,
                        box=Box(x0=10, y0=100, x1=500, y1=300),
                        cells=(
                            TableCell(row=0, col=0, box=Box(x0=10, y0=100, x1=250, y1=200)),
                            TableCell(row=0, col=1, box=Box(x0=250, y0=100, x1=500, y1=200)),
                        ),
                    ),
                ]
            ),
            engines=engines
            or {
                "trocr": ScriptedOcrEngine("trocr", ["neural network", "a", "b"], 0.8),
                "paddle": ScriptedOcrEngine("paddle", ["neural netwark", "a", "b"], 0.6),
            },
            transform=FakePageTransform(),
            calibrations=mem.calibrations,
            word_list=None,
            runtime=mem.runtime,
            orientation=OrientationPolicy(enabled=False),
        )
        while not reader.step(self.college_id, BookletId(UUID(booklet_id))):
            pass


@pytest.fixture
def s(client: TestClient, backends: MemoryBackends, world: AuthWorld) -> Setup:
    cid, admin_id = world.register("COLLEGE_A", "admin@a.example")
    world.invite(cid, admin_id, "teacher@a.example")
    teacher = login(client, "COLLEGE_A", "teacher@a.example", TEACHER_PASSWORD)
    admin = login(client, "COLLEGE_A", "admin@a.example", ADMIN_PASSWORD)
    imported = client.post(
        f"{BASE}/roster/import", content=ROSTER, headers={**admin, "Content-Type": "text/csv"}
    )
    assert imported.status_code == 200, imported.text
    students = [x["id"] for x in client.get(f"{BASE}/students", headers=admin).json()]
    users = list(backends.mem.users.list(cid))
    fixture = CollegeFixture(
        college=backends.mem.colleges.get(cid), teacher=users[1], admin=users[0], students=()
    )
    blueprint = ci_shaped_blueprint(backends.mem, fixture)
    unit = unit_for(backends.mem, cid, blueprint.id, owner_id=users[1].id)
    return Setup(client, backends, cid, teacher, admin, students, str(blueprint.id), str(unit))


@pytest.fixture
def other(client: TestClient, world: AuthWorld) -> dict[str, str]:
    """An SME of college B (the persona that may open booklets)."""
    cid, admin_id = world.register("COLLEGE_B", "admin@b.example")
    world.invite(cid, admin_id, "sme@b.example")
    return login(client, "COLLEGE_B", "sme@b.example", TEACHER_PASSWORD)


@pytest.fixture
def other_admin(client: TestClient, world: AuthWorld) -> dict[str, str]:
    world.register("COLLEGE_B", "admin@b.example")
    return login(client, "COLLEGE_B", "admin@b.example", ADMIN_PASSWORD)


# --- upload -----------------------------------------------------------------------------------


def test_upload_requires_signing_in(s: Setup) -> None:
    response = s.client.post(f"{BASE}/units/{s.unit_id}/booklets", files=[])
    assert response.status_code == 401


def test_a_pdf_upload_queues_the_booklet_in_the_unit(s: Setup) -> None:
    response = s.send(("tst001.PDF", fake_pdf(3)))
    assert response.status_code == 200, response.text
    report = response.json()
    assert (report["queued"], report["refused"], report["not_queued"]) == (1, 0, 0)
    (result,) = report["files"]
    assert result["name"] == "tst001.PDF" and result["usn"] == "TST001"
    assert result["problem"] is None and result["message"] is None
    body = s.client.get(f"{BASE}/booklets/{result['booklet_id']}", headers=s.teacher).json()
    assert body["status"] == "uploaded" and body["unit"]["id"] == s.unit_id
    assert body["student"]["usn"] == "TST001" and body["student"]["name"] == "Asha Test"
    assert body["blueprint"]["id"] == s.blueprint_id and body["blueprint"]["version"] == 1
    assert body["page_count"] == 0 and body["pages_cleaned"] == 0 and body["flagged_pages"] == []
    assert body["failure_reason"] is None
    assert len(s.backends.mem.jobs.jobs) == 1  # queued for the worker


def test_each_file_is_reported_and_only_valid_files_are_stored(s: Setup) -> None:
    response = s.send(
        ("C:\\scans\\TST001.pdf", fake_pdf(1, "a")),
        ("NOBODY01.pdf", fake_pdf(1, "b")),
        ("TST002.jpg", fake_image("x")),
        ("TST002.pdf", b"plain text"),
    )
    assert response.status_code == 200, response.text
    files = response.json()["files"]
    assert [f["outcome"] for f in files] == ["queued", "refused", "refused", "refused"]
    assert [f["problem"] for f in files[1:]] == ["unknown_usn", "not_pdf", "not_pdf"]
    assert "Students" in files[1]["message"]
    assert len(s.backends.mem.booklets.list(s.college_id)) == 1


def test_bad_requests_are_refused(s: Setup) -> None:
    assert s.send().status_code == 422
    s.backends.upload_limits = UploadLimits(max_batch_files=2)
    three = [(f"{n}.pdf", fake_pdf(1, str(n))) for n in range(3)]
    assert s.send(*three).status_code == 413
    s.backends.upload_limits = UploadLimits(max_total_bytes=50)
    assert s.send(("TST001.pdf", fake_pdf(1, "x" * 100))).status_code == 413
    assert s.client.get(f"{BASE}/booklets", headers=s.teacher).json()["total"] == 0


def test_the_same_file_or_student_again_is_refused(s: Setup) -> None:
    pdf = fake_pdf(2)
    s.upload(pdf)
    again = s.send(("TST001.pdf", fake_pdf(2, "other")), ("TST002.pdf", pdf)).json()["files"]
    assert [f["problem"] for f in again] == ["already_in_unit", "uploaded_before"]


def test_files_beyond_the_free_queue_slots_are_not_queued(s: Setup) -> None:
    s.backends.upload_limits = UploadLimits(max_waiting=1)
    report = s.send(("TST001.pdf", fake_pdf(1, "1")), ("TST002.pdf", fake_pdf(1, "2"))).json()
    assert [f["outcome"] for f in report["files"]] == ["queued", "not_queued"]
    assert report["files"][1]["problem"] == "queue_full"
    assert "again later" in report["files"][1]["message"]
    assert s.send(("TST002.pdf", fake_pdf(1, "2")), headers=s.teacher).status_code == 403
    listing = s.client.get(f"{BASE}/booklets", headers=s.admin).json()
    assert listing["waiting"] == 1 and listing["max_waiting"] == 1


def test_another_colleges_unit_is_not_found(s: Setup, other_admin: dict[str, str]) -> None:
    files = ("TST001.pdf", fake_pdf(1))
    assert s.send(files, headers=other_admin).status_code == 404
    assert s.send(files, unit="00000000-0000-0000-0000-00000000dead").status_code == 404


# --- status -----------------------------------------------------------------------------------


def test_status_follows_the_pipeline(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2))
    url = f"{BASE}/booklets/{booklet['id']}"
    assert s.client.get(url, headers=s.teacher).json()["status"] == "uploaded"
    s.process(booklet["id"])
    body = s.client.get(url, headers=s.teacher).json()
    assert body["status"] == "pages_ready" and body["page_count"] == 2
    assert body["pages_cleaned"] == 2 and body["flagged_pages"] == []
    first = body["pages"][0]
    assert first["number"] == 1 and first["cleaned"] and first["retake_reasons"] == []
    assert first["sharpness"] == 400.0 and first["glare_share"] == 0.0
    assert first["rotation_degrees"] == 0 and first["rotation_guessed"] is False
    assert first["image_url"] == f"{url}/pages/1/image"
    assert first["original_url"] == f"{url}/pages/1/image?kind=original"


def test_flagged_pages_carry_their_reasons(s: Setup) -> None:
    booklet = s.upload(fake_pdf(3, page_tags=("fine", "blur", "glare-blur")))
    s.process(booklet["id"])
    body = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert body["status"] == "needs_retake" and body["flagged_pages"] == [2, 3]
    assert [p["retake_reasons"] for p in body["pages"]] == [[], ["blurry"], ["blurry", "glare"]]


def test_an_unreadable_file_shows_as_failed_with_a_reason(s: Setup) -> None:
    booklet = s.upload(b"%PDF-bad")
    s.process(booklet["id"])
    body = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert body["status"] == "failed" and body["failure_reason"] == "unreadable_file"


def test_the_list_is_newest_first_filterable_and_paged(s: Setup) -> None:
    ids = []
    for n in range(4):
        ids.append(s.upload(fake_pdf(1, str(n)), student=n % 2)["id"])
        if n % 2:  # one booklet per student in a unit: a new unit for the next pair
            paper = BlueprintId(UUID(s.blueprint_id))
            s.unit_id = str(unit_for(s.backends.mem, s.college_id, paper, year=str(n)))
        s.backends.mem.clock.advance(minutes=1)
    s.process(ids[0])
    everything = s.client.get(f"{BASE}/booklets", headers=s.teacher).json()
    assert [b["id"] for b in everything["items"]] == ids[::-1] and everything["total"] == 4
    assert s.client.get(f"{BASE}/booklets?mine=true", headers=s.teacher).json()["total"] == 0
    mine = s.client.get(f"{BASE}/booklets?mine=true", headers=s.admin).json()
    assert {b["id"] for b in mine["items"]} == set(ids)
    ready = s.client.get(f"{BASE}/booklets?status=pages_ready", headers=s.teacher).json()
    assert [b["id"] for b in ready["items"]] == [ids[0]]
    page = s.client.get(f"{BASE}/booklets?limit=2&offset=1", headers=s.teacher).json()
    assert [b["id"] for b in page["items"]] == [ids[2], ids[1]] and page["total"] == 4
    assert s.client.get(f"{BASE}/booklets?status=nonsense", headers=s.teacher).status_code == 422


# --- page images ------------------------------------------------------------------------------


def test_page_images_are_served_to_signed_in_users_only(s: Setup) -> None:
    booklet = s.upload(fake_pdf(1, page_tags=("p",)))
    url = f"{BASE}/booklets/{booklet['id']}/pages/1/image"
    assert s.client.get(url, headers=s.teacher).status_code == 404  # not cleaned yet: no image
    original = s.client.get(url + "?kind=original", headers=s.teacher)
    assert original.status_code == 404  # no page rows until the split
    s.process(booklet["id"])
    cleaned = s.client.get(url, headers=s.teacher)
    assert cleaned.status_code == 200 and cleaned.content == b"CLEAN:" + fake_image("p")
    assert cleaned.headers["content-type"] == "image/jpeg"
    assert cleaned.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in cleaned.headers["cache-control"]
    assert s.client.get(url + "?kind=original", headers=s.teacher).content == fake_image("p")
    assert s.client.get(url).status_code == 401
    assert (
        s.client.get(
            f"{BASE}/booklets/{booklet['id']}/pages/9/image", headers=s.teacher
        ).status_code
        == 404
    )
    assert s.client.get(url + "?kind=other", headers=s.teacher).status_code == 422


# --- use anyway and delete --------------------------------------------------------------------


def test_use_anyway_moves_the_booklet_on_when_the_last_flag_is_overridden(s: Setup) -> None:
    booklet = s.upload(fake_pdf(3, page_tags=("fine", "blur", "glare")))
    s.process(booklet["id"])
    base = f"{BASE}/booklets/{booklet['id']}/pages"
    first = s.client.post(f"{base}/2/use-anyway", headers=s.admin)  # the admin's call (P26)
    assert first.status_code == 200 and first.json()["status"] == "needs_retake"
    assert first.json()["flagged_pages"] == [3]
    assert s.client.post(f"{base}/3/use-anyway", headers=s.teacher).status_code == 403
    last = s.client.post(f"{base}/3/use-anyway", headers=s.admin)
    assert last.json()["status"] == "pages_ready" and last.json()["flagged_pages"] == []
    detail = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert [p["use_anyway"] for p in detail["pages"]] == [False, True, True]
    assert detail["pages"][1]["retake_reasons"] == ["blurry"]  # the flag is kept, overridden
    events = [e for e in s.backends.mem.audit.events if e.action is AuditAction.PAGE_USED_ANYWAY]
    assert len(events) == 2


def test_use_anyway_refuses_a_page_that_passed_or_a_booklet_not_yet_processed(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2, page_tags=("fine", "blur")))
    base = f"{BASE}/booklets/{booklet['id']}/pages"
    assert s.client.post(f"{base}/2/use-anyway", headers=s.admin).status_code == 422
    s.process(booklet["id"])
    assert s.client.post(f"{base}/1/use-anyway", headers=s.admin).status_code == 422
    assert s.client.post(f"{base}/7/use-anyway", headers=s.admin).status_code == 422
    assert s.client.post(f"{base}/0/use-anyway", headers=s.admin).status_code == 422


def test_the_admin_sees_a_flagged_page_only_while_it_waits_for_a_retake(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2, page_tags=("fine", "blur")))
    s.process(booklet["id"])
    url = f"{BASE}/booklets/{booklet['id']}"
    pages = s.client.get(url, headers=s.admin).json()["pages"]
    assert [p["image_url"] is not None for p in pages] == [False, True]
    assert all(p["original_url"] is None and p["text_url"] is None for p in pages)
    assert s.client.get(f"{url}/pages/2/image", headers=s.admin).status_code == 200
    assert s.client.get(f"{url}/pages/1/image", headers=s.admin).status_code == 404
    original = s.client.get(f"{url}/pages/2/image?kind=original", headers=s.admin)
    assert original.status_code == 404
    s.client.post(f"{url}/pages/2/use-anyway", headers=s.admin)
    assert s.client.get(f"{url}/pages/2/image", headers=s.admin).status_code == 404


def test_a_retake_replaces_the_booklet(s: Setup) -> None:
    booklet = s.upload(fake_pdf(1, page_tags=("blur",)))
    url = f"{BASE}/booklets/{booklet['id']}/replace"
    new_pdf = [("file", ("TST001.pdf", fake_pdf(1, "retake"), "application/pdf"))]
    assert s.client.post(url, headers=s.admin, files=new_pdf).status_code == 409  # not cleaned
    s.process(booklet["id"])
    assert s.client.post(url, headers=s.teacher, files=new_pdf).status_code == 403
    replaced = s.client.post(url, headers=s.admin, files=new_pdf)
    assert replaced.status_code == 201, replaced.text
    body = replaced.json()
    assert body["status"] == "uploaded" and body["student"]["usn"] == "TST001"
    assert body["unit"]["id"] == s.unit_id and body["id"] != booklet["id"]
    gone = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher)
    assert gone.status_code == 404


def test_deleting_a_booklet_removes_it_and_its_images(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2))
    s.process(booklet["id"])
    assert s.backends.mem.blobs.keys
    url = f"{BASE}/booklets/{booklet['id']}"
    assert s.client.delete(url, headers=s.teacher).status_code == 204
    assert s.client.get(url, headers=s.teacher).status_code == 404
    assert s.backends.mem.blobs.keys == ()
    assert s.client.delete(url, headers=s.teacher).status_code == 404
    deleted = [e for e in s.backends.mem.audit.events if e.action is AuditAction.BOOKLET_DELETED]
    assert len(deleted) == 1 and deleted[0].before is None and deleted[0].after is None


# --- isolation --------------------------------------------------------------------------------


def test_another_college_sees_nothing_of_the_booklet(s: Setup, other: dict[str, str]) -> None:
    booklet = s.upload(fake_pdf(1))
    s.process(booklet["id"])
    url = f"{BASE}/booklets/{booklet['id']}"
    assert s.client.get(url, headers=other).status_code == 404
    assert s.client.get(f"{url}/pages/1/image", headers=other).status_code == 404
    # use-anyway is the admin's: an SME of B is refused for the role first
    assert s.client.post(f"{url}/pages/1/use-anyway", headers=other).status_code == 403
    assert s.client.delete(url, headers=other).status_code == 404
    listing = s.client.get(f"{BASE}/booklets", headers=other).json()
    assert listing["items"] == [] and listing["total"] == 0 and listing["waiting"] == 0
    assert s.client.get(url, headers=s.teacher).status_code == 200  # still there


# --- OCR (P10) -----------------------------------------------------------------------------------


def test_reading_shows_every_engine_and_the_chosen_line(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2))
    s.process(booklet["id"])
    before = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert before["pages_read"] == 0 and before["pages"][0]["text_url"] is None
    s.read(booklet["id"])
    detail = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert detail["status"] == "text_ready" and detail["pages_read"] == 2
    assert detail["needs_text_pages"] == []
    page = detail["pages"][0]
    assert page["text_read"] and page["text_url"].endswith("/pages/1/text")
    text = s.client.get(page["text_url"], headers=s.teacher).json()
    assert text["number"] == 1 and text["text_read"]
    line, table, *cells = text["regions"]
    assert line["kind"] == "text_line" and line["text"] == "neural network"
    # Readings in the configured engine order (the print set names paddle first).
    assert line["read_by"] == ["paddle", "trocr"] and line["flagged"] is False
    assert [r["engine"] for r in line["readings"]] == ["paddle", "trocr"]
    chosen = line["readings"][line["chosen"]]
    assert chosen["score"] == max(r["score"] for r in line["readings"])
    assert table["kind"] == "table"
    assert [(c["row"], c["col"], c["parent_id"]) for c in cells] == [
        (0, 0, table["id"]),
        (0, 1, table["id"]),
    ]
    assert (
        s.client.get(f"{BASE}/booklets/{booklet['id']}/pages/9/text", headers=s.teacher).status_code
        == 404
    )


def test_a_page_no_engine_read_is_listed(s: Setup) -> None:
    booklet = s.upload(fake_pdf(1))
    s.process(booklet["id"])
    s.read(booklet["id"], {"trocr": ScriptedOcrEngine("trocr", ["x"], fail=EngineTimeoutError())})
    detail = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert detail["needs_text_pages"] == [1]
    assert detail["pages"][0]["ocr_failures"] == ["trocr:timeout"]


def test_page_text_of_another_college_is_not_found(s: Setup, other: dict[str, str]) -> None:
    booklet = s.upload(fake_pdf(1))
    s.process(booklet["id"])
    s.read(booklet["id"])
    response = s.client.get(f"{BASE}/booklets/{booklet['id']}/pages/1/text", headers=other)
    assert response.status_code == 404


# --- segmentation (P12) ---------------------------------------------------------------------------


def test_a_segmented_booklet_and_a_failed_segmentation_are_served(s: Setup) -> None:
    booklet = s.upload(fake_pdf(1))
    s.process(booklet["id"])
    s.read(booklet["id"])
    mem = s.backends.mem
    booklet_id = BookletId(UUID(booklet["id"]))
    segmenter = BookletSegmenter(
        booklets=mem.booklets,
        content=mem.content,
        embedder=TrigramEmbedder(),
        runtime=mem.runtime,
        jobs=mem.jobs,
    )
    assert segmenter.step(s.college_id, booklet_id)
    url = f"{BASE}/booklets/{booklet['id']}"
    assert s.client.get(url, headers=s.teacher).json()["status"] == "segmented"

    failed = s.upload(fake_pdf(1, "second"), student=1)
    s.process(failed["id"])
    s.read(failed["id"])
    segmenter.abandon(s.college_id, BookletId(UUID(failed["id"])))
    detail = s.client.get(f"{BASE}/booklets/{failed['id']}", headers=s.teacher).json()
    assert detail["status"] == "failed" and detail["failure_reason"] == "segmentation_failed"


# --- personas (P24) ----------------------------------------------------------------------------


def test_the_admin_sees_progress_but_no_pages_text_or_marks(s: Setup) -> None:
    booklet = s.upload(fake_pdf(1))
    s.process(booklet["id"])
    s.read(booklet["id"])
    url = f"{BASE}/booklets/{booklet['id']}"
    admin_view = s.client.get(url, headers=s.admin).json()
    assert admin_view["status"] == "text_ready" and admin_view["result"] is None
    (page,) = admin_view["pages"]
    assert page["image_url"] is None and page["original_url"] is None and page["text_url"] is None
    sme_view = s.client.get(url, headers=s.teacher).json()["pages"][0]
    assert sme_view["image_url"] and sme_view["text_url"]
    assert s.client.get(url + "/pages/1/image", headers=s.admin).status_code == 404  # not flagged
    assert s.client.get(url + "/pages/1/text", headers=s.admin).status_code == 403
    assert s.client.delete(url, headers=s.admin).status_code == 403
    listed = s.client.get(f"{BASE}/booklets", headers=s.admin).json()["items"]
    assert [b["id"] for b in listed] == [booklet["id"]] and listed[0]["result"] is None


def test_an_sme_sees_only_the_booklets_of_their_subjects(s: Setup) -> None:
    booklet = s.upload(fake_pdf(1))
    mem = s.backends.mem
    sme = next(u for u in mem.users.list(s.college_id) if u.role.value == "sme")
    for subject in mem.entitlements.subjects_of(s.college_id, sme.id):
        mem.entitlements.revoke(s.college_id, sme.id, subject)
    url = f"{BASE}/booklets/{booklet['id']}"
    assert s.client.get(url, headers=s.teacher).status_code == 404
    assert s.client.get(f"{url}/pages/1/image", headers=s.teacher).status_code == 404
    assert s.client.get(f"{BASE}/booklets", headers=s.teacher).json()["total"] == 0
    assert s.client.get(url, headers=s.admin).status_code == 200  # the admin sees the college


def test_an_sme_sees_only_the_booklets_of_their_own_units(s: Setup, world: AuthWorld) -> None:
    mem = s.backends.mem
    admin = next(u for u in mem.users.list(s.college_id) if u.role.value == "admin")
    second = world.invite(s.college_id, admin.id, "second@a.example")
    paper = UUID(s.blueprint_id)
    for subject in mem.entitlements.subjects_of(
        s.college_id, s.backends.mem.users.list(s.college_id)[1].id
    ):
        mem.entitlements.grant(
            s.college_id,
            SubjectGrant(
                college_id=s.college_id,
                user_id=second,
                subject_id=subject,
                granted_by=admin.id,
                granted_at=mem.clock.now(),
            ),
        )
    theirs = unit_for(mem, s.college_id, BlueprintId(paper), owner_id=second, year="2025")
    sent = s.send(("TST001.pdf", fake_pdf(1, "theirs")), unit=str(theirs)).json()
    url = f"{BASE}/booklets/{sent['files'][0]['booklet_id']}"
    assert s.client.get(url, headers=s.teacher).status_code == 404  # same subject, not my unit
    assert s.client.get(f"{BASE}/booklets", headers=s.teacher).json()["total"] == 0
    assert s.client.get(f"{BASE}/units/{theirs}", headers=s.teacher).status_code == 404
    second_sme = login(s.client, "COLLEGE_A", "second@a.example", TEACHER_PASSWORD)
    assert s.client.get(url, headers=second_sme).status_code == 200
    assert s.client.get(url, headers=s.admin).status_code == 200
