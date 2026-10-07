"""P21 item 6: the teacher's whole journey with five queued booklets, on the in-memory adapters
(the PostgreSQL + MinIO run is ``test_e2e_journey_postgres.py``). See ``tarn_api.journey``."""

from uuid import UUID

from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import QP_CI_PAGE, scripted_stages
from tarn_api.app import create_app
from tarn_api.journey import Event, Journey, event_payload
from tarn_api.testing import MemoryBackends
from tarn_core.ids import BookletId, CollegeId
from tarn_core.testing.seed_world import seed_in_memory


def test_teacher_journey_with_five_queued_booklets() -> None:
    backends = MemoryBackends()
    mem = backends.mem
    seed_in_memory(mem)
    app = create_app(Settings(_env_file=None, device="cpu"), backends=backends)

    def process() -> None:
        # One worker, one job at a time, until the queue is empty (D61).
        errors = LocalStageRunner(scripted_stages(mem.blobs), mem).run(
            CollegeId(UUID(int=0)), BookletId(UUID(int=0))
        )
        assert errors == []

    def events(college: UUID) -> list[Event]:
        return [
            Event(e.action.value, e.booklet_id, event_payload(e.before, e.after))
            for e in mem.audit.events
            if e.college_id == college
        ]

    # What the scripted OCR "reads" (the answers' text, without the line-syntax markers).
    lines = [line.lstrip("^> ") for line in QP_CI_PAGE]
    with TestClient(app) as client:
        journey = Journey(client, process, events, student_texts=lines)
        result = journey.run()

    assert journey.log.count("booklet.registered") == 5
    assert journey.log.count("result_sheet.issued") == 6  # five v1, one v2

    gone = result["deleted"]
    assert not [k for k in mem.blobs.keys if gone in k.value]  # every file of it went
    kept = result["booklets"][0]
    assert [k for k in mem.blobs.keys if kept in k.value and "/sheets/" in k.value]
