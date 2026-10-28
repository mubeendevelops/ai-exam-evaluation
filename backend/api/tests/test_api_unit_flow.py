"""P28 over HTTP: submit, return, freeze and content deletion (D164, D166). Every write route
on a submitted unit's booklets and on the unit itself is swept and must answer 409; after the
freeze the same, and the frozen record and the result sheets outlive a deleted paper."""

from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.postgres.testing import create_test_database, drop_test_database
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_adapters.seed.runner import run_seed
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import scripted_stages
from tarn_api.app import create_app
from tarn_api.backends import PostgresBackends, unit_of_work
from tarn_api.tenancy_sweep import (
    A_INSTITUTION,
    B_INSTITUTION,
    BASE,
    CASES,
    World,
    build_world,
    login,
    url,
)
from tarn_api.testing import MemoryBackends
from tarn_core.ids import BookletId, CollegeId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.services.pipeline import QualityPolicy
from tarn_core.testing import FakePageCleaner, FakePageSplitter, MemoryMailer
from tarn_core.testing.auth_world import AuthWorld
from tarn_core.testing.seed_world import seed_in_memory
from tarn_worker.booklets import BookletJobRunner

GOVERNOR = "governor@demo-com.example.test"
B_GOVERNOR = "governor@demo-eng.example.test"
NOTE = "Recheck Q3 for every booklet"

# Writes that stay allowed on a closed unit: closing a booklet changes nothing; the admin's
# return and the Governor's freeze are what move a submitted unit on.
STILL_ALLOWED = {
    ("DELETE", "/api/v1/booklets/{booklet_id}/lock"),
    ("POST", "/api/v1/units/{unit_id}/return"),
    ("POST", "/api/v1/units/{unit_id}/freeze"),
}


def _unit_writes() -> list[tuple[str, str]]:
    """Every write on a booklet or a unit (by id in the path)."""
    return [
        (method, path)
        for (method, path) in CASES
        if method != "GET"
        and ("{booklet_id}" in path or "{unit_id}" in path)
        and (method, path) not in STILL_ALLOWED
    ]


class Flow:
    def __init__(self, world: World, mail: MemoryMailer, governor: dict[str, str]) -> None:
        self.w = world
        self.mail = mail
        self.governor = governor
        self.c = world.client
        self.unit = world.ids["unit_id"]
        self.b_governor: dict[str, str] | None = None
        """College B's Governor (the PostgreSQL run): A's unit is not found for them."""

    def detail(self) -> dict[str, Any]:
        r = self.c.get(f"{BASE}/units/{self.unit}", headers=self.w.a)
        assert r.status_code == 200, r.text
        return dict(r.json())

    def unmarked(self) -> list[dict[str, str]]:
        return [
            {"booklet_id": b["id"], "reason": "unreadable"}
            for b in self.detail()["booklets"]
            if b["status"] not in ("approved", "approved_amended")
        ]

    def submit(self) -> Any:
        return self.c.post(
            f"{BASE}/units/{self.unit}/submit",
            json={"unmarked": self.unmarked()},
            headers=self.w.a,
        )

    def sweep(self, word: str) -> dict[tuple[str, str], int]:
        """Each write as A's own SME or admin: what did not answer 409 with ``word``."""
        wrong: dict[tuple[str, str], int] = {}
        for method, path in _unit_writes():
            case = CASES[(method, path)]
            headers = dict(
                self.governor
                if case.as_governor
                else (self.w.a_admin if case.as_admin else self.w.a)
            )
            extra = case.request(self.w) if case.request else {}
            headers |= extra.pop("headers", {})
            response = self.c.request(method, url(self.w, path), headers=headers, **extra)
            if response.status_code != 409 or word not in response.json()["detail"]:
                wrong[(method, path)] = response.status_code
        return wrong


@pytest.fixture(scope="module")
def flow_pg(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Flow]:
    base = Settings()
    app_db = create_test_database(base.database_url, base.app_database_url)
    identity_db = create_test_identity_database(base.database_url, base.identity_app_database_url)
    settings = Settings(
        app_database_url=app_db.app_url,
        identity_app_database_url=identity_db.app_url,
        local_key_dir=tmp_path_factory.mktemp("keys"),
        device="cpu",
        rate_limits_enabled=False,
    )
    backends = PostgresBackends(settings)
    mail = MemoryMailer()
    backends.kit = replace(backends.kit, mailer=mail)
    run_seed(settings, blobs=backends._blobs)
    runner = BookletJobRunner(
        db=backends._app,
        blobs=backends._blobs,
        splitter=FakePageSplitter(),
        cleaner=FakePageCleaner(),
        clock=SystemClock(),
        ids=UuidGenerator(),
        policy=QualityPolicy(),
        max_pages=40,
        worker="p28",
        ocr=scripted_stages(backends._blobs).ocr,
    )

    def process() -> None:
        for _ in range(200):
            if not runner.run_one():
                return

    try:
        with TestClient(create_app(settings, backends=backends)) as client:
            world = build_world(client, process)
            b_college = client.get(f"{BASE}/auth/me", headers=world.b).json()["user"]["college_id"]
            for college, email in (
                (CollegeId(UUID(world.ids["a_college"])), GOVERNOR),
                (CollegeId(UUID(b_college)), B_GOVERNOR),
            ):
                with unit_of_work(backends, college) as unit:
                    unit.accounts.invite_governor(
                        college, display_name="Governor", email=email, operator="test"
                    )
                accepted = client.post(
                    f"{BASE}/auth/invitations/accept",
                    json={"token": mail.last_token(email), "password": DEV_PASSWORD},
                )
                assert accepted.status_code == 204, accepted.text
            flow = Flow(world, mail, login(client, A_INSTITUTION, GOVERNOR))
            flow.b_governor = login(client, B_INSTITUTION, B_GOVERNOR)
            yield flow
    finally:
        backends.dispose()
        drop_test_database(base.database_url, app_db.name)
        drop_test_database(base.database_url, identity_db.name)


@pytest.fixture(scope="module")
def flow() -> Iterator[Flow]:
    backends = MemoryBackends()
    mem = backends.mem
    seed_in_memory(mem)
    tenant = mem.identity.find_tenant(A_INSTITUTION)
    assert tenant is not None
    AuthWorld(mem).accounts.invite_governor(
        tenant.college_id, display_name="Demo Governor", email=GOVERNOR, operator="test"
    )
    AuthWorld(mem).auth.accept_invite(
        tenant.college_id, mem.mailer.last_token(GOVERNOR), DEV_PASSWORD
    )
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )

    def process() -> None:
        runner = LocalStageRunner(scripted_stages(mem.blobs), mem)
        assert runner.run(CollegeId(UUID(int=0)), BookletId(UUID(int=0))) == []

    with TestClient(app) as client:
        world = build_world(client, process)
        yield Flow(world, mem.mailer, login(client, A_INSTITUTION, GOVERNOR))


def test_p28_lifecycle(flow: Flow) -> None:
    lifecycle(flow)


@pytest.mark.integration
def test_p28_lifecycle_on_postgres(flow_pg: Flow) -> None:
    """The same on PostgreSQL (``tarn_app``, row-level security, the 0016 triggers) and MinIO,
    with B's Governor refused A's unit (the sweep's two Governor routes, D174)."""
    lifecycle(flow_pg)


def lifecycle(flow: Flow) -> None:
    """One story, in order: refusals before submission, submission, the sweep, return,
    resubmission, freeze, the sweep again, deletion and what outlives it."""
    c, w, unit = flow.c, flow.w, flow.unit

    # A reason of kind "other" needs a note (and the booklet is approved: no reason at all).
    other = c.post(
        f"{BASE}/units/{unit}/submit",
        json={"unmarked": [{"booklet_id": w.ids["booklet_id"], "reason": "other"}]},
        headers=w.a,
    )
    assert other.status_code == 422  # "other" needs a note, and the booklet is approved
    assert c.post(f"{BASE}/units/{unit}/freeze", json={}, headers=flow.governor).status_code == 409

    submitted = flow.submit()
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["state"] == "submitted" and body["submission"]["returned_at"] is None
    assert body["pending_request"] is None  # the SME's request was withdrawn
    # Reading stays open; every write is refused.
    booklet = c.get(f"{BASE}/booklets/{w.ids['booklet_id']}", headers=w.a).json()
    assert booklet["unit_state"] == "submitted"
    assert c.get(f"{BASE}/booklets/{w.ids['booklet_id']}/review", headers=w.a).status_code == 200
    assert flow.sweep("submitted") == {}

    # The Governor lists it; the admin returns it with a note; the SME is emailed.
    listed = c.get(f"{BASE}/units", headers=flow.governor).json()
    assert [u["id"] for u in listed] == [unit]
    returned = c.post(f"{BASE}/units/{unit}/return", json={"note": NOTE}, headers=w.a_admin)
    assert returned.status_code == 200, returned.text
    assert returned.json()["state"] == "open"
    assert returned.json()["submission"]["return_note"] == NOTE
    assert NOTE in flow.mail.sent[-1].text
    assert c.get(f"{BASE}/units", headers=flow.governor).json() == []

    # Open again: a write goes through (the SME may open the booklet), then resubmit.
    assert c.post(f"{BASE}/booklets/{w.ids['booklet_id']}/lock", headers=w.a).status_code == 200
    again = flow.submit()
    assert again.status_code == 200 and again.json()["submission"]["return_note"] == ""

    # Freeze and sign off: the frozen record; then nothing changes.
    if flow.b_governor is not None:  # another college's Governor: not found (D174)
        for r in (
            c.post(f"{BASE}/units/{unit}/freeze", json={}, headers=flow.b_governor),
            c.get(f"{BASE}/units/{unit}/frozen-record", headers=flow.b_governor),
        ):
            assert r.status_code == 404 and r.json() == {"detail": "Not found."}
    frozen = c.post(f"{BASE}/units/{unit}/freeze", json={}, headers=flow.governor)
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["state"] == "frozen" and frozen.json()["frozen"]["frozen_by"]["name"]
    record = c.get(f"{BASE}/units/{unit}/frozen-record", headers=flow.governor).json()
    mine = next(b for b in record["booklets"] if b["booklet_id"] == w.ids["booklet_id"])
    assert mine["outcome"] == "approved" and mine["sheets"] and mine["answers"]
    assert all(a["sme_mark"] is not None for a in mine["answers"])
    assert flow.sweep("frozen") == {}
    for method, path, headers in (
        ("POST", f"/units/{unit}/return", w.a_admin),
        ("POST", f"/units/{unit}/freeze", flow.governor),
    ):
        body_json = {"note": NOTE} if path.endswith("return") else {}
        r = c.request(method, BASE + path, json=body_json, headers=headers)
        assert r.status_code == 409, (path, r.text)

    # Delete the paper (frozen now), then the question it linked.
    paper = w.ids["blueprint_id"]
    check = c.get(f"{BASE}/blueprints/{paper}/deletion", headers=w.a).json()
    assert check == {"allowed": True, "blockers": []}
    question = _a_linked_question(flow)
    blocked = c.get(f"{BASE}/questions/{question}/deletion", headers=w.a).json()
    assert not blocked["allowed"] and "links it" in blocked["blockers"][0]
    assert c.delete(f"{BASE}/questions/{question}", headers=w.a).status_code == 409
    assert c.delete(f"{BASE}/blueprints/{paper}", headers=w.a).status_code == 204
    assert c.get(f"{BASE}/blueprints/{paper}", headers=w.a).status_code == 404
    still = c.get(f"{BASE}/questions/{question}/deletion", headers=w.a).json()
    if still["allowed"]:  # no other paper links it
        assert c.delete(f"{BASE}/questions/{question}", headers=w.a).status_code == 204

    # What outlives the paper: the frozen record, the evaluated list, the PDF, the unit list.
    assert c.get(f"{BASE}/units/{unit}/frozen-record", headers=flow.governor).status_code == 200
    evaluated = c.get(f"{BASE}/evaluated-booklets", headers=w.a).json()["items"]
    found = next(e for e in evaluated if e["id"] == w.ids["booklet_id"])
    pdf = c.get(found["sheets"][-1]["pdf_url"], headers=w.a)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert c.get(f"{BASE}/units/{unit}", headers=w.a).json()["paper"]["id"] == paper
    gone = c.get(f"{BASE}/booklets/{w.ids['booklet_id']}/review", headers=w.a)
    assert gone.status_code == 410 and "frozen record remain" in gone.json()["detail"]


def _a_linked_question(flow: Flow) -> str:
    paper = flow.c.get(f"{BASE}/blueprints/{flow.w.ids['blueprint_id']}", headers=flow.w.a).json()

    def ids(node: Any) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "question_id" and isinstance(value, str):
                    yield value
                else:
                    yield from ids(value)
        elif isinstance(node, list):
            for item in node:
                yield from ids(item)

    return next(ids(paper["document"]))
