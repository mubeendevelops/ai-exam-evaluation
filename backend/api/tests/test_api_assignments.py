"""The SME's own figures over HTTP (P31, D216): ``GET /me/assignments/summary`` on the demo
seed, after a transfer, for the SME only, their own units only."""

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
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
SUMMARY = f"{BASE}/me/assignments/summary"


class Demo:
    def __init__(self, client: TestClient) -> None:
        self.client = client
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


@pytest.fixture
def demo() -> Iterator[Demo]:
    backends = MemoryBackends()
    seed_in_memory(backends.mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )
    with TestClient(app) as client:
        yield Demo(client)


def test_the_sme_reads_the_figures_of_their_own_units(demo: Demo) -> None:
    mine = demo.get(f"{BASE}/units", demo.suresh)
    found = demo.get(SUMMARY, demo.suresh)
    assert found["units_lifetime"] == len(mine) > 0
    states = [u["state"] for u in mine]
    assert found["breakdown"] == {
        "open": states.count("open"),
        "submitted": states.count("submitted"),
        "frozen": states.count("frozen"),
    }
    assert found["active"] == states.count("open")
    assert found["completed"] == found["units_lifetime"] - found["active"]
    # The seed's units are all open: nothing handed in, so no turnaround yet.
    assert found["completion_rate"] == 0.0
    assert (found["average_turnaround_days"], found["turnaround_units"]) == (None, 0)
    years = [academic_year_of(date.fromisoformat(u["due_date"])) for u in mine]
    assert found["units_current_year"] == sum(
        1 for y in years if y.label == found["academic_year"]["label"]
    )
    assert found["academic_year"]["start"].endswith("-07-01")


def test_a_transferred_unit_counts_for_its_new_owner_only(demo: Demo) -> None:
    before = demo.get(SUMMARY, demo.suresh)
    other = demo.get(SUMMARY, demo.anita)
    anita_id = demo.get(f"{BASE}/auth/me", demo.anita)["user"]["id"]
    unit = demo.get(f"{BASE}/units", demo.suresh)[0]
    moved = demo.client.post(
        f"{BASE}/units/{unit['id']}/transfer",
        headers=demo.admin,
        json={"owner_id": anita_id, "reason": "Balance the load"},
    )
    assert moved.status_code == 200, moved.text
    assert demo.get(SUMMARY, demo.suresh)["units_lifetime"] == before["units_lifetime"] - 1
    assert demo.get(SUMMARY, demo.anita)["units_lifetime"] == other["units_lifetime"] + 1


def test_another_colleges_sme_counts_only_their_own(demo: Demo) -> None:
    theirs = demo.get(f"{BASE}/units", demo.ravi)
    assert demo.get(SUMMARY, demo.ravi)["units_lifetime"] == len(theirs)


def test_the_admin_is_refused(demo: Demo) -> None:
    response = demo.client.get(SUMMARY, headers=demo.admin)
    assert response.status_code == 403
    assert response.json() == {"detail": ROLE_REFUSED}
    assert demo.client.get(SUMMARY).status_code == 401
