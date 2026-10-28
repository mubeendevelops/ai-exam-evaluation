"""The admin's dashboard, SME progress report and health box over HTTP (P29, D163, D167):
the figures on the demo seed, after a transfer and a new SME, only for the admin, only for
the caller's college, and never a mark or a speed."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.testing import MemoryBackends
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
SURESH = "suresh.naik@demo-com.example.test"
ANITA = "anita.desai@demo-com.example.test"
BANNED = ("mark", "score", "speed", "pace", "accuracy", "per_hour", "grade")


class Demo:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.admin = self.login("DEMO_COM", "admin@demo-com.example.test")
        self.suresh = self.login("DEMO_COM", SURESH)
        self.eng_admin = self.login("DEMO_ENG", "admin@demo-eng.example.test")

    def login(self, institution: str, email: str) -> dict[str, str]:
        response = self.client.post(
            f"{BASE}/auth/login",
            json={"institution_id": institution, "email": email, "password": DEV_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def get(self, path: str, who: dict[str, str]) -> Any:
        response = self.client.get(f"{BASE}{path}", headers=who)
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


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


def test_the_dashboard_counts_the_seed(demo: Demo) -> None:
    found = demo.get("/overview", demo.admin)
    assert found["smes"] == {"active": 2, "idle": 0, "suspended": 0}
    assert (found["units"]["open"], found["units"]["submitted"], found["units"]["frozen"]) == (
        4,
        0,
        0,
    )
    stages = found["booklets"]
    assert stages["total"] == sum(
        stages[k] for k in ("processing", "ready", "in_review", "approved", "attention")
    )
    progress = demo.get("/overview/progress", demo.admin)
    assert stages["total"] == sum(u["booklets"]["total"] for u in progress["units"])
    assert stages["approved"] == sum(s["approved"] for s in progress["smes"])


def test_the_progress_report_lists_every_sme_and_unit(demo: Demo) -> None:
    progress = demo.get("/overview/progress", demo.admin)
    assert [s["display_name"] for s in progress["smes"]] == [
        "Anita Desai (demo SME)",
        "Suresh Naik (demo SME)",
    ]
    assert {s["units_owned"] for s in progress["smes"]} == {2}
    assert len(progress["units"]) == 4
    unit = progress["units"][0]
    assert unit["owner_name"] and unit["paper_title"] and unit["subject_code"]
    assert unit["state"] == "open" and unit["code"].startswith("U-")


def test_credit_follows_a_transferred_unit_and_idle_is_computed(demo: Demo) -> None:
    c = demo.client
    smes = {s["display_name"]: s for s in demo.get("/overview/progress", demo.admin)["smes"]}
    suresh, anita = smes["Suresh Naik (demo SME)"], smes["Anita Desai (demo SME)"]
    units = demo.get("/units", demo.admin)
    mine = next(u for u in units if u["owner"]["id"] == suresh["user_id"])
    moved = c.post(
        f"{BASE}/units/{mine['id']}/transfer",
        headers=demo.admin,
        json={"owner_id": anita["user_id"], "reason": "Balance the load"},
    )
    assert moved.status_code == 200, moved.text
    after = {s["display_name"]: s for s in demo.get("/overview/progress", demo.admin)["smes"]}
    assert after["Suresh Naik (demo SME)"]["units_owned"] == suresh["units_owned"] - 1
    assert after["Anita Desai (demo SME)"]["units_owned"] == anita["units_owned"] + 1
    assert (
        after["Anita Desai (demo SME)"]["booklets"] == anita["booklets"] + mine["counts"]["total"]
    )
    unit_row = next(
        u for u in demo.get("/overview/progress", demo.admin)["units"] if u["unit_id"] == mine["id"]
    )
    assert unit_row["owner_id"] == anita["user_id"]
    feed = demo.get("/overview", demo.admin)["activity"]
    assert feed[0]["action"] == "unit.transferred" and feed[0]["unit_code"] == mine["code"]

    # A new SME owns nothing yet: Idle, in the dashboard and the directory.
    invited = c.post(
        f"{BASE}/accounts",
        headers=demo.admin,
        json={"display_name": "Newcomer", "email": "newcomer@demo-com.example.test"},
    )
    assert invited.status_code == 201 and invited.json()["idle"] is True
    assert demo.get("/overview", demo.admin)["smes"]["idle"] == 1
    flags = {a["user"]["display_name"]: a["idle"] for a in demo.get("/accounts", demo.admin)}
    assert flags["Newcomer"] is True
    assert flags["Anita Desai (demo SME)"] is False and flags["Suresh Naik (demo SME)"] is False

    # Suspended wins over idle; the dashboard counts it apart.
    new_id = invited.json()["user"]["id"]
    assert c.post(f"{BASE}/accounts/{new_id}/disable", headers=demo.admin).status_code == 200
    assert demo.get("/overview", demo.admin)["smes"] == {"active": 2, "idle": 0, "suspended": 1}
    flags = {a["user"]["display_name"]: a["idle"] for a in demo.get("/accounts", demo.admin)}
    assert flags["Newcomer"] is False


def test_the_health_box_reads_the_system_figures(demo: Demo) -> None:
    system = demo.get("/overview/system", demo.admin)
    assert set(system) == {"queue_queued", "queue_running", "storage_bytes"}
    assert all(isinstance(v, int) and v >= 0 for v in system.values())
    assert demo.client.get(f"{BASE}/health").json()["worker"]["status"] in {"up", "down", "unknown"}


def test_only_the_admin_sees_the_figures(demo: Demo) -> None:
    for path in ("/overview", "/overview/progress", "/overview/system"):
        refused = demo.client.get(f"{BASE}{path}", headers=demo.suresh)
        assert refused.status_code == 403
        assert demo.client.get(f"{BASE}{path}").status_code == 401


def test_each_colleges_admin_sees_only_their_own(demo: Demo) -> None:
    ours = demo.get("/overview/progress", demo.admin)
    theirs = demo.get("/overview/progress", demo.eng_admin)
    assert {s["user_id"] for s in ours["smes"]}.isdisjoint({s["user_id"] for s in theirs["smes"]})
    assert {u["unit_id"] for u in ours["units"]}.isdisjoint({u["unit_id"] for u in theirs["units"]})
    assert "Anita Desai (demo SME)" not in {s["display_name"] for s in theirs["smes"]}


def test_no_response_carries_a_mark_or_a_speed(demo: Demo) -> None:
    """D167. The responses and their published schemas have no field named for marks."""
    for path in ("/overview", "/overview/progress", "/overview/system"):
        for key in _keys(demo.get(path, demo.admin)):
            assert not any(word in key for word in BANNED), (path, key)
    schemas = demo.client.get("/openapi.json").json()["components"]["schemas"]
    for name in (
        "DashboardOut",
        "ProgressOut",
        "SystemOut",
        "SmeProgressOut",
        "UnitProgressOut",
        "StageCountsOut",
        "ActivityOut",
    ):
        assert not any(w in p for p in schemas[name]["properties"] for w in BANNED), name


def test_the_feed_has_no_student_data(demo: Demo) -> None:
    feed = demo.get("/overview", demo.admin)["activity"]
    assert feed
    assert all(set(item) == {"at", "action", "actor_name", "unit_code"} for item in feed)
    assert not any("usn" in str(item).lower() for item in feed)
