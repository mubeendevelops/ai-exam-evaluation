"""P30 item 2: the admin portal's whole story, from a new college to a frozen unit, on the
in-memory adapters (the PostgreSQL + MinIO run is in ``test_e2e_postgres.py``). See
``tarn_api.admin_journey``."""

from uuid import UUID

from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import scripted_stages
from tarn_api.admin_journey import JOURNEY_PAGE, AdminJourney
from tarn_api.app import create_app
from tarn_api.journey import Event, event_payload
from tarn_api.testing import MemoryBackends
from tarn_core.ids import BookletId, CollegeId


def test_admin_portal_journey_from_a_new_college_to_a_frozen_unit() -> None:
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
        journey = AdminJourney(client, backends, process, events, mem.mailer)
        result = journey.run()

    assert journey.log.count("result_sheet.issued") == 6  # five v1, one v2
    assert journey.log.count("unit.submitted") == 2
    for booklet in result["booklets"]:  # the sheets outlive the deleted paper
        assert [k for k in mem.blobs.keys if booklet in k.value and "/sheets/" in k.value]
    assert {"/units/papers", "/overview/progress"} <= set(journey.admin_reads)
