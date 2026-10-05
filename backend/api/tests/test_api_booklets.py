"""Booklet upload, page-cleaning status and the teacher's decisions over HTTP. In-memory
backends; the image work is faked (tiny byte strings), the pipeline is the real core one."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_api.testing import MemoryBackends
from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.errors import EngineTimeoutError
from tarn_core.ids import BookletId, CollegeId
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
from tarn_core.testing.builders import CollegeFixture, ci_shaped_blueprint

BASE = "/api/v1"
ROSTER = "name,usn,class/section\nAsha Test,TST001,A\nBala Test,TST002,A\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"png"


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

    def upload(
        self,
        *files: bytes,
        headers: dict[str, str] | None = None,
        student: int = 0,
        **form: str,
    ) -> Any:
        return self.client.post(
            f"{BASE}/booklets",
            headers=headers or self.teacher,
            data={"student_id": self.students[student], "blueprint_id": self.blueprint_id, **form},
            files=[
                ("files", (f"page{n}", data, "application/octet-stream"))
                for n, data in enumerate(files)
            ],
        )

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
        f"{BASE}/roster/import", content=ROSTER, headers={**teacher, "Content-Type": "text/csv"}
    )
    assert imported.status_code == 200, imported.text
    students = [x["id"] for x in client.get(f"{BASE}/students", headers=teacher).json()]
    users = list(backends.mem.users.list(cid))
    fixture = CollegeFixture(
        college=backends.mem.colleges.get(cid), teacher=users[1], admin=users[0], students=()
    )
    blueprint = ci_shaped_blueprint(backends.mem, fixture)
    return Setup(client, backends, cid, teacher, admin, students, str(blueprint.id))


@pytest.fixture
def other(client: TestClient, world: AuthWorld) -> dict[str, str]:
    world.register("COLLEGE_B", "admin@b.example")
    return login(client, "COLLEGE_B", "admin@b.example", ADMIN_PASSWORD)


# --- upload -----------------------------------------------------------------------------------


def test_upload_requires_signing_in(s: Setup) -> None:
    response = s.client.post(f"{BASE}/booklets", data={}, files=[])
    assert response.status_code == 401


def test_a_pdf_upload_returns_the_queued_booklet(s: Setup) -> None:
    response = s.upload(fake_pdf(3))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["student"]["usn"] == "TST001" and body["student"]["name"] == "Asha Test"
    assert body["blueprint"]["id"] == s.blueprint_id and body["blueprint"]["version"] == 1
    assert body["page_count"] == 0 and body["pages_cleaned"] == 0 and body["flagged_pages"] == []
    assert body["failure_reason"] is None and body["duplicate_of"] == []
    assert len(s.backends.mem.jobs.jobs) == 1  # queued for the worker


def test_images_in_order_become_one_booklet(s: Setup) -> None:
    response = s.upload(fake_image("a"), PNG, fake_image("c"))
    assert response.status_code == 201, response.text
    mem = s.backends.mem
    (booklet,) = mem.booklets.list(s.college_id)
    assert [x.media_type for x in booklet.sources] == ["image/jpeg", "image/png", "image/jpeg"]


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ([b"plain text"], 415),
        ([fake_pdf(1), fake_image()], 422),
        ([fake_pdf(1), fake_pdf(1, "x")], 422),
        ([], 422),
    ],
)
def test_bad_uploads_are_refused(s: Setup, files: list[bytes], code: int) -> None:
    response = s.upload(*files)
    assert response.status_code == code, response.text
    assert s.client.get(f"{BASE}/booklets", headers=s.teacher).json()["total"] == 0


def test_an_oversized_upload_is_refused(s: Setup) -> None:
    s.backends.upload_limits = UploadLimits(max_total_bytes=50, max_files=3)
    assert s.upload(fake_image("x" * 100)).status_code == 413
    assert s.upload(*[fake_image(str(n)) for n in range(4)]).status_code == 413
    assert s.upload(fake_image("ok")).status_code == 201


def test_the_same_file_is_a_duplicate_unless_confirmed(s: Setup) -> None:
    pdf = fake_pdf(2)
    first = s.upload(pdf).json()
    refused = s.upload(pdf, student=1)
    assert refused.status_code == 409
    assert refused.json()["duplicate_of"] == [first["id"]]
    allowed = s.upload(pdf, student=1, allow_duplicate="true")
    assert allowed.status_code == 201
    assert allowed.json()["duplicate_of"] == [first["id"]]


def test_a_teacher_may_have_a_limited_number_waiting(s: Setup) -> None:
    s.backends.upload_limits = UploadLimits(max_waiting=2)
    first = s.upload(fake_pdf(1, "1")).json()
    assert s.upload(fake_pdf(1, "2")).status_code == 201
    full = s.upload(fake_pdf(1, "3"))
    assert full.status_code == 429 and "waiting" in full.json()["detail"]
    assert s.upload(fake_pdf(1, "4"), headers=s.admin).status_code == 201  # another user
    listing = s.client.get(f"{BASE}/booklets", headers=s.teacher).json()
    assert listing["waiting"] == 2 and listing["max_waiting"] == 2
    s.process(first["id"])
    assert s.upload(fake_pdf(1, "5")).status_code == 429  # clean pages still wait for OCR
    s.read(first["id"])
    assert s.upload(fake_pdf(1, "5")).status_code == 201  # a slot freed up
    assert s.client.get(f"{BASE}/booklets", headers=s.teacher).json()["waiting"] == 2


def test_the_student_and_exam_must_belong_to_the_college(
    s: Setup, other: dict[str, str], client: TestClient
) -> None:
    students_b = client.post(
        f"{BASE}/roster/import",
        content=ROSTER.replace("TST", "OTH"),
        headers={**other, "Content-Type": "text/csv"},
    )
    assert students_b.status_code == 200
    foreign = client.get(f"{BASE}/students", headers=other).json()[0]["id"]
    response = client.post(
        f"{BASE}/booklets",
        headers=s.teacher,
        data={"student_id": foreign, "blueprint_id": s.blueprint_id},
        files=[("files", ("f", fake_pdf(1), "application/pdf"))],
    )
    assert response.status_code == 404
    unknown = client.post(
        f"{BASE}/booklets",
        headers=s.teacher,
        data={"student_id": s.students[0], "blueprint_id": "00000000-0000-0000-0000-00000000dead"},
        files=[("files", ("f", fake_pdf(1), "application/pdf"))],
    )
    assert unknown.status_code == 404


# --- status -----------------------------------------------------------------------------------


def test_status_follows_the_pipeline(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2)).json()
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
    booklet = s.upload(fake_image("fine"), fake_image("blur"), fake_image("glare-blur")).json()
    s.process(booklet["id"])
    body = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert body["status"] == "needs_retake" and body["flagged_pages"] == [2, 3]
    assert [p["retake_reasons"] for p in body["pages"]] == [[], ["blurry"], ["blurry", "glare"]]


def test_an_unreadable_file_shows_as_failed_with_a_reason(s: Setup) -> None:
    booklet = s.upload(b"%PDF-bad").json()
    s.process(booklet["id"])
    body = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert body["status"] == "failed" and body["failure_reason"] == "unreadable_file"


def test_the_list_is_newest_first_filterable_and_paged(s: Setup) -> None:
    ids = []
    for n in range(4):
        ids.append(
            s.upload(fake_pdf(1, str(n)), headers=s.teacher if n % 2 == 0 else s.admin).json()["id"]
        )
        s.backends.mem.clock.advance(minutes=1)
    s.process(ids[0])
    everything = s.client.get(f"{BASE}/booklets", headers=s.teacher).json()
    assert [b["id"] for b in everything["items"]] == ids[::-1] and everything["total"] == 4
    mine = s.client.get(f"{BASE}/booklets?mine=true", headers=s.teacher).json()
    assert {b["id"] for b in mine["items"]} == {ids[0], ids[2]}
    ready = s.client.get(f"{BASE}/booklets?status=pages_ready", headers=s.teacher).json()
    assert [b["id"] for b in ready["items"]] == [ids[0]]
    page = s.client.get(f"{BASE}/booklets?limit=2&offset=1", headers=s.teacher).json()
    assert [b["id"] for b in page["items"]] == [ids[2], ids[1]] and page["total"] == 4
    assert s.client.get(f"{BASE}/booklets?status=nonsense", headers=s.teacher).status_code == 422


# --- page images ------------------------------------------------------------------------------


def test_page_images_are_served_to_signed_in_users_only(s: Setup) -> None:
    booklet = s.upload(fake_image("p")).json()
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
    booklet = s.upload(fake_image("fine"), fake_image("blur"), fake_image("glare")).json()
    s.process(booklet["id"])
    base = f"{BASE}/booklets/{booklet['id']}/pages"
    first = s.client.post(f"{base}/2/use-anyway", headers=s.teacher)
    assert first.status_code == 200 and first.json()["status"] == "needs_retake"
    assert first.json()["flagged_pages"] == [3]
    last = s.client.post(f"{base}/3/use-anyway", headers=s.admin)
    assert last.json()["status"] == "pages_ready" and last.json()["flagged_pages"] == []
    detail = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert [p["use_anyway"] for p in detail["pages"]] == [False, True, True]
    assert detail["pages"][1]["retake_reasons"] == ["blurry"]  # the flag is kept, overridden
    events = [e for e in s.backends.mem.audit.events if e.action is AuditAction.PAGE_USED_ANYWAY]
    assert len(events) == 2


def test_use_anyway_refuses_a_page_that_passed_or_a_booklet_not_yet_processed(s: Setup) -> None:
    booklet = s.upload(fake_image("fine"), fake_image("blur")).json()
    base = f"{BASE}/booklets/{booklet['id']}/pages"
    assert s.client.post(f"{base}/2/use-anyway", headers=s.teacher).status_code == 422
    s.process(booklet["id"])
    assert s.client.post(f"{base}/1/use-anyway", headers=s.teacher).status_code == 422
    assert s.client.post(f"{base}/7/use-anyway", headers=s.teacher).status_code == 422
    assert s.client.post(f"{base}/0/use-anyway", headers=s.teacher).status_code == 422


def test_deleting_a_booklet_removes_it_and_its_images(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2)).json()
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
    booklet = s.upload(fake_pdf(1)).json()
    s.process(booklet["id"])
    url = f"{BASE}/booklets/{booklet['id']}"
    assert s.client.get(url, headers=other).status_code == 404
    assert s.client.get(f"{url}/pages/1/image", headers=other).status_code == 404
    assert s.client.post(f"{url}/pages/1/use-anyway", headers=other).status_code == 404
    assert s.client.delete(url, headers=other).status_code == 404
    listing = s.client.get(f"{BASE}/booklets", headers=other).json()
    assert listing["items"] == [] and listing["total"] == 0 and listing["waiting"] == 0
    assert s.client.get(url, headers=s.teacher).status_code == 200  # still there


# --- OCR (P10) -----------------------------------------------------------------------------------


def test_reading_shows_every_engine_and_the_chosen_line(s: Setup) -> None:
    booklet = s.upload(fake_pdf(2)).json()
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
    booklet = s.upload(fake_pdf(1)).json()
    s.process(booklet["id"])
    s.read(booklet["id"], {"trocr": ScriptedOcrEngine("trocr", ["x"], fail=EngineTimeoutError())})
    detail = s.client.get(f"{BASE}/booklets/{booklet['id']}", headers=s.teacher).json()
    assert detail["needs_text_pages"] == [1]
    assert detail["pages"][0]["ocr_failures"] == ["trocr:timeout"]


def test_page_text_of_another_college_is_not_found(s: Setup, other: dict[str, str]) -> None:
    booklet = s.upload(fake_pdf(1)).json()
    s.process(booklet["id"])
    s.read(booklet["id"])
    response = s.client.get(f"{BASE}/booklets/{booklet['id']}/pages/1/text", headers=other)
    assert response.status_code == 404


# --- segmentation (P12) ---------------------------------------------------------------------------


def test_a_segmented_booklet_and_a_failed_segmentation_are_served(s: Setup) -> None:
    booklet = s.upload(fake_pdf(1)).json()
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

    failed = s.upload(fake_pdf(1, "second")).json()
    s.process(failed["id"])
    s.read(failed["id"])
    segmenter.abandon(s.college_id, BookletId(UUID(failed["id"])))
    detail = s.client.get(f"{BASE}/booklets/{failed['id']}", headers=s.teacher).json()
    assert detail["status"] == "failed" and detail["failure_reason"] == "segmentation_failed"
