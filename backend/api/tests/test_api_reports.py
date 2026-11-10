"""My Reports over HTTP (P36, D226, D239): ``GET /me/reports`` on the demo seed and after a
transfer, for the SME only, their own units only, with no accuracy, speed or mark in it. The
quarters, the on-time rule and the credit after a transfer are held by ``test_reports`` and the
PostgreSQL contract test."""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.security import ROLE_REFUSED
from tarn_api.testing import MemoryBackends
from tarn_core.domain.assignments import academic_year_of
from tarn_core.domain.reports import last_quarters
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
REPORTS = f"{BASE}/me/reports"


class Demo:
    def __init__(self, client: TestClient, backends: MemoryBackends) -> None:
        self.client = client
        self.today = backends.mem.clock.now().date()
        self.admin = self.login("DEMO_COM", "admin@demo-com.example.test")
        self.suresh = self.login("DEMO_COM", "suresh.naik@demo-com.example.test")
        self.anita = self.login("DEMO_COM", "anita.desai@demo-com.example.test")
        self.ravi = self.login("DEMO_ENG", "ravi.menon@demo-eng.example.test")

    def login(self, institution: str, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": institution, "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def get(self, path: str, who: dict[str, str]) -> Any:
        response = self.client.get(path, headers=who)
        assert response.status_code == 200, response.text
        return response.json()

    def pending_of(self, who: dict[str, str]) -> tuple[int, int]:
        """Open units and booklets not completed in them, from the unit list."""
        open_units = [u for u in self.get(f"{BASE}/units", who) if u["state"] == "open"]
        return len(open_units), sum(
            u["counts"]["total"] - u["counts"]["completed"] for u in open_units
        )


@pytest.fixture
def demo() -> Iterator[Demo]:
    backends = MemoryBackends()
    seed_in_memory(backends.mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )
    with TestClient(app) as client:
        yield Demo(client, backends)


def test_the_sme_reads_the_report_of_their_own_units(demo: Demo) -> None:
    found = demo.get(REPORTS, demo.suresh)
    year = academic_year_of(demo.today)
    assert found["academic_year"] == {
        "start": year.start.isoformat(),
        "end": year.end.isoformat(),
        "label": year.label,
    }
    # The seed's units are all open: nothing handed in yet.
    assert found["turnaround_lifetime"] == {"average_days": None, "units": 0}
    assert found["turnaround_year"] == {"average_days": None, "units": 0}
    assert (found["on_time_rate"], found["units_handed_in"]) == (None, 0)
    assert (found["open_units"], found["booklets_pending"]) == demo.pending_of(demo.suresh)
    assert found["open_units"] > 0


def test_the_last_four_calendar_quarters_end_with_the_current_one(demo: Demo) -> None:
    quarters = demo.get(REPORTS, demo.suresh)["quarters"]
    want = last_quarters(demo.today)
    assert [q["label"] for q in quarters] == [q.label for q in want]
    assert [(q["start"], q["end"]) for q in quarters] == [
        (q.start.isoformat(), q.end.isoformat()) for q in want
    ]
    assert (
        date.fromisoformat(quarters[-1]["start"])
        <= demo.today
        < date.fromisoformat(quarters[-1]["end"])
    )
    assert all((q["units_submitted"], q["booklets_approved"]) == (0, 0) for q in quarters[:-1])


def test_a_transferred_unit_moves_its_load_to_the_new_owner(demo: Demo) -> None:
    before = demo.get(REPORTS, demo.suresh), demo.get(REPORTS, demo.anita)
    anita_id = demo.get(f"{BASE}/auth/me", demo.anita)["user"]["id"]
    unit = next(u for u in demo.get(f"{BASE}/units", demo.suresh) if u["state"] == "open")
    pending = unit["counts"]["total"] - unit["counts"]["completed"]
    moved = demo.client.post(
        f"{BASE}/units/{unit['id']}/transfer",
        headers=demo.admin,
        json={"owner_id": anita_id, "reason": "Balance the load"},
    )
    assert moved.status_code == 200, moved.text
    after = demo.get(REPORTS, demo.suresh), demo.get(REPORTS, demo.anita)
    assert after[0]["open_units"] == before[0]["open_units"] - 1
    assert after[1]["open_units"] == before[1]["open_units"] + 1
    assert after[0]["booklets_pending"] == before[0]["booklets_pending"] - pending
    assert after[1]["booklets_pending"] == before[1]["booklets_pending"] + pending


def test_another_colleges_sme_counts_only_their_own(demo: Demo) -> None:
    assert demo.get(REPORTS, demo.ravi)["open_units"] == demo.pending_of(demo.ravi)[0]


def test_nothing_about_accuracy_speed_or_marks_is_reported(demo: Demo) -> None:
    def keys(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [k for k, v in value.items()] + [k for v in value.values() for k in keys(v)]
        if isinstance(value, list):
            return [k for v in value for k in keys(v)]
        return []

    found = keys(demo.get(REPORTS, demo.suresh))
    for word in ("accuracy", "speed", "mark", "score", "total", "per_hour"):
        assert not [k for k in found if word in k], word


def test_the_admin_is_refused(demo: Demo) -> None:
    response = demo.client.get(REPORTS, headers=demo.admin)
    assert response.status_code == 403
    assert response.json() == {"detail": ROLE_REFUSED}
    assert demo.client.get(REPORTS).status_code == 401
