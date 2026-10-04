# mypy: disable-error-code="no-untyped-call"
# (PyMuPDF ships no type information for its document API.)
"""The API on PostgreSQL: tarn_app on a throwaway application database, tarn_auth on a
throwaway identity database (``make up`` first; ``make test-integration``)."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.auth.mail import ConsoleMailer
from tarn_adapters.config import Settings
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.ocr.transform import OpenCvPageTransform
from tarn_adapters.postgres.testing import create_test_database, drop_test_database
from tarn_api.app import create_app
from tarn_api.backends import PostgresBackends
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.domain.ocr import SelectorSettings
from tarn_core.ports.engines import DetectedRegion
from tarn_core.ports.identity import EmailMessage
from tarn_core.services.ocr.reader import OrientationPolicy
from tarn_core.testing import ScriptedLayoutDetector, ScriptedOcrEngine
from tarn_worker.booklets import OcrKit

pytestmark = pytest.mark.integration

PASSWORD = "a long integration passphrase"


class CapturingMailer(ConsoleMailer):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def send(self, message: EmailMessage) -> None:
        self.links += [w for w in message.text.split() if "token=" in w]


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[TestClient, CapturingMailer]]:
    base = Settings()
    app_db = create_test_database(base.database_url, base.app_database_url)
    identity_db = create_test_identity_database(base.database_url, base.identity_app_database_url)
    keys: Path = tmp_path_factory.mktemp("keys")
    settings = Settings(
        app_database_url=app_db.app_url,
        identity_app_database_url=identity_db.app_url,
        local_key_dir=keys,
        tenant_signup_requires_approval=False,
        device="cpu",
    )
    backends = PostgresBackends(settings)
    mailer = CapturingMailer()
    backends.kit = replace(backends.kit, mailer=mailer)
    try:
        with TestClient(create_app(settings, backends=backends)) as client:
            yield client, mailer
    finally:
        backends.dispose()
        drop_test_database(base.database_url, app_db.name)
        drop_test_database(base.database_url, identity_db.name)


def _register(client: TestClient, mailer: CapturingMailer, institution: str, email: str) -> str:
    form = {"institution_id": institution, "admin_name": "Admin", "email": email}
    r = client.post("/api/v1/registrations", json=form | {"password": PASSWORD})
    assert r.status_code == 202, r.text
    token = mailer.links[-1].split("token=")[1]
    assert (
        client.post("/api/v1/registrations/verify-email", json={"token": token}).json()["status"]
        == "ACTIVE"
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"institution_id": institution, "email": email, "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    return str(login.json()["access_token"])


def test_two_colleges_over_http_on_postgres(setup: tuple[TestClient, CapturingMailer]) -> None:
    client, mailer = setup
    a = {"Authorization": f"Bearer {_register(client, mailer, 'PG_A', 'admin@pg-a.example')}"}
    b = {"Authorization": f"Bearer {_register(client, mailer, 'PG_B', 'admin@pg-b.example')}"}
    csv = "name,usn,class/section\nSynthetic Student,1PG21CS001,A\n"
    r = client.post("/api/v1/roster/import", content=csv, headers=a | {"Content-Type": "text/csv"})
    assert r.json()["created"] == 1
    assert [s["usn"] for s in client.get("/api/v1/students", headers=a).json()] == ["1PG21CS001"]
    assert client.get("/api/v1/students", headers=b).json() == []
    invited = client.post(
        "/api/v1/accounts", json={"display_name": "T", "email": "t@pg-a.example"}, headers=a
    )
    assert invited.status_code == 201
    teacher_id = invited.json()["user"]["id"]
    assert client.post(f"/api/v1/accounts/{teacher_id}/disable", headers=b).status_code == 404
    emails_b = [x["user"]["email"] for x in client.get("/api/v1/accounts", headers=b).json()]
    assert emails_b == ["admin@pg-b.example"]
    # Lockout persists across requests (counters committed although sign-in failed).
    wrong = {"institution_id": "PG_B", "email": "admin@pg-b.example", "password": "nope nope"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    right = wrong | {"password": PASSWORD}
    assert client.post("/api/v1/auth/login", json=right).status_code == 401


def test_question_bank_with_real_database_and_minio(
    setup: tuple[TestClient, CapturingMailer],
) -> None:
    """Two colleges: A creates a question with key, rubric and files; B reads it (global),
    cannot edit it, copies it. Files really go to MinIO under global/keys/."""
    client, mailer = setup
    a = {"Authorization": f"Bearer {_register(client, mailer, 'PG_QA', 'admin@pg-qa.example')}"}
    b = {"Authorization": f"Bearer {_register(client, mailer, 'PG_QB', 'admin@pg-qb.example')}"}
    subject = client.post(
        "/api/v1/subjects", json={"code": "PHY", "name": "Physics"}, headers=a
    ).json()["id"]
    created = client.post(
        "/api/v1/questions",
        json={
            "subject_id": subject,
            "code": "PG-Q1",
            "text": "Why is the sky blue?",
            "max_marks": 4,
            "difficulty": "easy",
            "category": "Optics",
            "reference_answer": "Rayleigh scattering.",
            "criteria": [
                {
                    "type": "semantic",
                    "label": "Cause",
                    "weight": 3,
                    "params": {"reference_statement": "Short wavelengths scatter more."},
                },
                {
                    "type": "list",
                    "label": "Names",
                    "weight": 1,
                    "params": {
                        "items": [{"term": "Rayleigh", "synonyms": ["scattering"]}],
                        "required_count": 1,
                    },
                },
            ],
        },
        headers=a,
    )
    assert created.status_code == 201, created.text
    qid = created.json()["id"]
    pdf = b"%PDF-1.7\nsynthetic key"
    up = client.post(
        f"/api/v1/questions/{qid}/key-files",
        params={"filename": "key.pdf", "confirm_no_student_data": "true", "keywords": ["sky"]},
        content=pdf,
        headers=a | {"Content-Type": "application/pdf"},
    )
    assert up.status_code == 201, up.text
    assert client.get(up.json()["content_url"], headers=b).content == pdf  # from MinIO

    seen = client.get(f"/api/v1/questions/{qid}", headers=b).json()
    assert seen["owned"] is False and seen["owner_name"] == "PG_QA"
    assert seen["rubric"]["complete"] is True and seen["key_count"] == 2
    edit = {"code": "PG-Q1", "text": "x", "max_marks": 4, "difficulty": "easy", "category": ""}
    assert client.put(f"/api/v1/questions/{qid}", json=edit, headers=b).status_code == 403

    page = client.get("/api/v1/questions", params={"keyword": "rayleigh"}, headers=b).json()
    assert [i["code"] for i in page["items"]] == ["PG-Q1"] and page["items"][0]["key_count"] == 2
    copy = client.post(f"/api/v1/questions/{qid}/copy", headers=b)
    assert copy.status_code == 201, copy.text
    assert copy.json()["owned"] is True and copy.json()["key_files"][0]["name"] == "key.pdf"
    mine = client.get("/api/v1/questions", params={"mine": "true"}, headers=b).json()
    assert [i["id"] for i in mine["items"]] == [copy.json()["id"]]


def test_booklet_upload_worker_and_status_with_real_database_and_minio(
    setup: tuple[TestClient, CapturingMailer],
) -> None:
    """Upload over HTTP, the worker's runner on the real queue, the cleaned pages in MinIO."""
    import pymupdf

    from tarn_adapters.imaging import testing as synth
    from tarn_adapters.imaging.cleaner import OpenCvPageCleaner
    from tarn_adapters.imaging.pdf import PyMuPdfSplitter
    from tarn_adapters.runtime import SystemClock, UuidGenerator
    from tarn_core.domain.common import BlobKey
    from tarn_core.ids import CollegeId
    from tarn_core.services.pipeline import QualityPolicy
    from tarn_core.testing.builders import CollegeFixture, ci_shaped_blueprint
    from tarn_worker.booklets import BookletJobRunner

    client, mailer = setup
    backends = client.app.state.backends  # type: ignore[attr-defined]
    a = {"Authorization": f"Bearer {_register(client, mailer, 'PG_BK', 'admin@pg-bk.example')}"}
    b = {"Authorization": f"Bearer {_register(client, mailer, 'PG_BK2', 'admin@pg-bk2.example')}"}
    me = client.get("/api/v1/auth/me", headers=a).json()
    college_id = CollegeId(UUID(me["user"]["college_id"]))
    csv = "name,usn,class/section\nSynthetic Student,1BK21CS001,A\n"
    client.post("/api/v1/roster/import", content=csv, headers=a | {"Content-Type": "text/csv"})
    student = client.get("/api/v1/students", headers=a).json()[0]["id"]
    ids, clock = UuidGenerator(), SystemClock()
    with backends._app.session(college_id, ids=ids, clock=clock, blobs=backends._blobs) as s:
        admin = s.users.list(college_id)[0]
        blueprint = ci_shaped_blueprint(
            s,
            CollegeFixture(
                college=s.colleges.get(college_id), teacher=admin, admin=admin, students=()
            ),
        )

    page = synth.ruled_page(900, 1200, lines=10)
    document = pymupdf.open()
    for shot in (
        synth.photograph(page, size=(900, 1200), margin=0.03),
        synth.scan(page, size=(900, 1200)),
    ):
        sheet = document.new_page(width=595, height=842)
        sheet.insert_image(sheet.rect, stream=synth.jpeg(shot))
    pdf: bytes = document.tobytes()

    def upload(**extra: str) -> object:
        return client.post(
            "/api/v1/booklets",
            headers=a,
            data={"student_id": student, "blueprint_id": str(blueprint.id), **extra},
            files=[("files", ("booklet.pdf", pdf, "application/pdf"))],
        )

    created = upload()
    assert created.status_code == 201, created.text  # type: ignore[attr-defined]
    booklet = created.json()  # type: ignore[attr-defined]
    assert booklet["status"] == "uploaded" and booklet["student"]["usn"] == "1BK21CS001"
    assert upload().status_code == 409  # type: ignore[attr-defined]  # same file again

    runner = BookletJobRunner(
        db=backends._app,
        blobs=backends._blobs,
        splitter=PyMuPdfSplitter(),
        cleaner=OpenCvPageCleaner(),
        clock=clock,
        ids=ids,
        policy=QualityPolicy(),
        max_pages=10,
        worker="api-test",
        ocr=OcrKit(
            layout=ScriptedLayoutDetector(
                [DetectedRegion(kind=RegionKind.TEXT_LINE, box=Box(x0=20, y0=20, x1=400, y1=80))]
            ),
            engines={"trocr": ScriptedOcrEngine("trocr", ["synthetic line"], 0.9)},
            transform=OpenCvPageTransform(),
            word_list=None,
            settings=SelectorSettings(),
            orientation=OrientationPolicy(enabled=False),
        ),
    )
    assert runner.run_one() is True  # page cleaning; the reading job is queued next

    detail = client.get(f"/api/v1/booklets/{booklet['id']}", headers=a).json()
    assert detail["status"] == "pages_ready" and detail["page_count"] == 2
    assert [p["cleaned"] for p in detail["pages"]] == [True, True]
    assert detail["pages"][0]["cropped"] is True and detail["pages"][1]["cropped"] is False
    cleaned = client.get(detail["pages"][0]["image_url"], headers=a)
    assert cleaned.status_code == 200 and cleaned.content[:3] == b"\xff\xd8\xff"
    original = client.get(detail["pages"][0]["original_url"], headers=a)
    assert original.status_code == 200 and original.content[:3] == b"\xff\xd8\xff"
    assert client.get("/api/v1/booklets", headers=a).json()["waiting"] == 1  # waits for OCR
    assert runner.run_one() is True  # reading
    read = client.get(f"/api/v1/booklets/{booklet['id']}", headers=a).json()
    assert read["status"] == "text_ready" and read["pages_read"] == 2
    assert runner.run_one() is True  # segmentation (P12)
    assert runner.run_one() is False
    segmented = client.get(f"/api/v1/booklets/{booklet['id']}", headers=a).json()
    assert segmented["status"] == "segmented"
    text = client.get(read["pages"][0]["text_url"], headers=a).json()
    assert [r["text"] for r in text["regions"]] == ["synthetic line"]
    assert client.get(read["pages"][0]["text_url"], headers=b).status_code == 404
    listing = client.get("/api/v1/booklets", headers=a).json()
    assert [x["id"] for x in listing["items"]] == [booklet["id"]] and listing["waiting"] == 0
    assert client.get(f"/api/v1/booklets/{booklet['id']}", headers=b).status_code == 404
    assert client.get(detail["pages"][0]["image_url"], headers=b).status_code == 404

    stored = [
        f"college/{college_id}/booklet/{booklet['id']}/source/001.pdf",
        f"college/{college_id}/booklet/{booklet['id']}/original/001.jpg",
        f"college/{college_id}/booklet/{booklet['id']}/clean/001.jpg",
        f"college/{college_id}/booklet/{booklet['id']}/clean/002.jpg",
    ]
    assert all(backends._blobs.exists(BlobKey(k)) for k in stored)
    assert client.delete(f"/api/v1/booklets/{booklet['id']}", headers=a).status_code == 204
    assert not any(backends._blobs.exists(BlobKey(k)) for k in stored)
