"""P38 item 2: the SME portal's whole story, from a schema to a frozen unit whose paper is
deleted, on the in-memory adapters (the PostgreSQL + MinIO run is in ``test_e2e_postgres.py``).
See ``tarn_api.sme_journey``."""

from uuid import UUID

from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import scripted_stages
from tarn_api.admin_journey import JOURNEY_PAGE
from tarn_api.app import create_app
from tarn_api.journey import Event, event_payload
from tarn_api.sme_journey import SmeJourney
from tarn_api.testing import MemoryBackends
from tarn_core.ids import BookletId, CollegeId


def test_sme_portal_journey_from_a_schema_to_a_frozen_unit() -> None:
    backends = MemoryBackends()
    mem = backends.mem
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )

    def process() -> None:
        errors = LocalStageRunner(scripted_stages(mem.blobs, JOURNEY_PAGE), mem).run(
            CollegeId(UUID(int=0)), BookletId(UUID(int=0))
        )
        assert errors == []

    def events(college: UUID) -> list[Event]:
        return [
            Event(e.action.value, e.booklet_id, event_payload(e.before, e.after))
            for e in mem.audit.events
            if e.college_id == college
        ]

    with TestClient(app) as client:
        journey = SmeJourney(client, backends, process, events, mem.mailer)
        result = journey.run()

    assert journey.log.count("result_sheet.issued") == 5
    assert journey.log.count("delegation.requested") == 1
    assert {"/delegation-requests", "/units/smes"} <= set(journey.admin_reads)
    for booklet in result["booklets"]:  # the sheets outlive the deleted paper
        assert [k for k in mem.blobs.keys if booklet in k.value and "/sheets/" in k.value]
