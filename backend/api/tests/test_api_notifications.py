"""Notifications and global search over HTTP (P37, D227, D228): the SME's own notices, newest
first and paged, read once; a transfer writes one notice for each SME it concerns; the search
returns the SME's own units, students and subjects, 2 to 80 characters, counted per user, and
its text never reaches the log. Scope and the sweep are held by the core contracts (memory and
PostgreSQL)."""

import logging
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_api.app import create_app
from tarn_api.ratelimit import SEARCH_USER, RateLimiter
from tarn_api.testing import MemoryBackends
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing.seed_world import seed_in_memory

BASE = "/api/v1"
NOTICES = f"{BASE}/notifications"


class Demo:
    def __init__(self, client: TestClient, backends: MemoryBackends) -> None:
        self.client = client
        self.backends = backends
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

    def get(self, path: str, who: dict[str, str], **params: Any) -> Any:
        response = self.client.get(path, headers=who, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def me(self, who: dict[str, str]) -> str:
        return str(self.get(f"{BASE}/auth/me", who)["user"]["id"])


def make(rate_limits: bool) -> Iterator[Demo]:
    backends = MemoryBackends()
    seed_in_memory(backends.mem)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=rate_limits),
        backends=backends,
        rate_limiter=RateLimiter(backends.mem.clock.now, enabled=rate_limits),
    )
    with TestClient(app) as client:
        yield Demo(client, backends)


@pytest.fixture
def demo() -> Iterator[Demo]:
    yield from make(False)


def test_the_seed_left_one_assigned_notice_per_unit(demo: Demo) -> None:
    page = demo.get(NOTICES, demo.suresh)
    units = list(demo.get(f"{BASE}/units", demo.suresh))
    assert page["total"] == page["unread"] == len(units) > 0
    assert {n["kind"] for n in page["items"]} == {"assigned"}
    assert {n["unit_id"] for n in page["items"]} == {u["id"] for u in units}
    first = page["items"][0]
    assert set(first) == {
        "id",
        "kind",
        "unit_id",
        "unit_code",
        "due_date",
        "request_id",
        "note",
        "created_at",
        "read_at",
    }
    assert demo.get(f"{NOTICES}/unread-count", demo.suresh) == {"unread": page["unread"]}


def test_a_transfer_notifies_both_smes_once_and_reading_is_per_user(demo: Demo) -> None:
    unit = demo.get(f"{BASE}/units", demo.suresh)[0]
    before = demo.get(NOTICES, demo.anita)["total"]
    moved = demo.client.post(
        f"{BASE}/units/{unit['id']}/transfer",
        json={"owner_id": demo.me(demo.anita), "reason": "Load balancing"},
        headers=demo.admin,
    )
    assert moved.status_code == 200, moved.text
    theirs = demo.get(NOTICES, demo.anita)
    assert theirs["total"] == before + 1
    assert (theirs["items"][0]["kind"], theirs["items"][0]["unit_id"]) == ("assigned", unit["id"])
    mine = demo.get(NOTICES, demo.suresh, limit=1)
    assert len(mine["items"]) == 1 and mine["limit"] == 1
    latest = mine["items"][0]
    assert (latest["kind"], latest["unit_code"]) == ("transferred_away", unit["code"])

    # Another SME's notice: 404, unread stays.
    other = demo.client.post(f"{NOTICES}/{theirs['items'][0]['id']}/read", headers=demo.suresh)
    assert other.status_code == 404
    assert demo.client.post(f"{NOTICES}/{uuid4()}/read", headers=demo.suresh).status_code == 404
    read = demo.client.post(f"{NOTICES}/{latest['id']}/read", headers=demo.suresh)
    assert read.status_code == 200 and read.json()["read_at"]
    count = demo.get(f"{NOTICES}/unread-count", demo.suresh)["unread"]
    assert count == mine["unread"] - 1
    marked = demo.client.post(f"{NOTICES}/read-all", headers=demo.suresh).json()
    assert marked == {"marked": count}
    assert demo.get(f"{NOTICES}/unread-count", demo.suresh) == {"unread": 0}
    assert demo.get(f"{NOTICES}/unread-count", demo.anita)["unread"] == theirs["unread"]


def test_paging_and_personas(demo: Demo) -> None:
    whole = demo.get(NOTICES, demo.suresh)
    second = demo.get(NOTICES, demo.suresh, limit=1, offset=1)
    assert second["items"][0]["id"] == whole["items"][1]["id"]
    for bad in ({"limit": 0}, {"limit": 51}, {"offset": -1}):
        r = demo.client.get(NOTICES, headers=demo.suresh, params=bad)
        assert r.status_code == 422
    for path in (NOTICES, f"{NOTICES}/unread-count"):
        assert demo.client.get(path, headers=demo.admin).status_code == 403
    assert demo.client.post(f"{NOTICES}/read-all", headers=demo.admin).status_code == 403
    # Another college's SME sees their own notices only.
    ravi = demo.get(NOTICES, demo.ravi)
    assert not {n["id"] for n in ravi["items"]} & {n["id"] for n in whole["items"]}
    stranger = demo.client.post(f"{NOTICES}/{whole['items'][0]['id']}/read", headers=demo.ravi)
    assert stranger.status_code == 404


# --- global search -------------------------------------------------------------------------


def test_the_search_finds_the_smes_own_units_and_students(demo: Demo) -> None:
    unit = demo.get(f"{BASE}/units", demo.suresh)[0]
    detail = demo.get(f"{BASE}/units/{unit['id']}", demo.suresh)
    found = demo.get(f"{BASE}/search", demo.suresh, q=unit["code"].lower())
    assert [u["unit_id"] for u in found["units"]] == [unit["id"]]
    assert set(found) == {"units", "students", "papers", "schemas", "questions"}
    if detail["booklets"]:
        student = detail["booklets"][0]["student"]
        hits = demo.get(f"{BASE}/search", demo.suresh, q=student["usn"])["students"]
        assert [h["booklet_id"] for h in hits][:1] == [detail["booklets"][0]["id"]]
        # Anita owns no unit with this student: nothing.
        assert demo.get(f"{BASE}/search", demo.anita, q=student["usn"])["students"] == []
    # Another college's SME never sees A's unit.
    theirs = demo.get(f"{BASE}/search", demo.ravi, q=unit["code"])
    assert all(u["unit_id"] != unit["id"] for u in theirs["units"])
    for group in found.values():
        assert len(group) <= 5


def test_the_search_refuses_bad_lengths_and_other_personas(demo: Demo) -> None:
    for q in ("x", "  y  ", "z" * 81):
        r = demo.client.get(f"{BASE}/search", headers=demo.suresh, params={"q": q})
        assert r.status_code == 422
        assert q.strip() not in r.text
    assert demo.client.get(f"{BASE}/search", headers=demo.suresh).status_code == 422
    assert (
        demo.client.get(f"{BASE}/search", headers=demo.admin, params={"q": "QP"}).status_code == 403
    )


def test_the_search_text_never_reaches_the_log(
    demo: Demo, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "Zyxwvut Qponml"
    with caplog.at_level(logging.DEBUG):
        demo.client.get(f"{BASE}/search", headers=demo.suresh, params={"q": secret})
        demo.client.get(f"{BASE}/search", headers=demo.suresh, params={"q": "x"})
    out = capsys.readouterr()
    # The test's own HTTP client logs the URLs it asks for; the server must log nothing of it.
    server = [r.getMessage() for r in caplog.records if not r.name.startswith("httpx")]
    lines = [x for x in (out.out + out.err).splitlines() if "HTTP Request:" not in x]
    for text in (*server, *lines):
        assert "Zyxwvut" not in text and "Qponml" not in text
    assert all(e.action.value != "search" for e in demo.backends.mem.audit.events)


@pytest.fixture
def limited() -> Iterator[Demo]:
    yield from make(True)


def test_the_search_is_counted_per_user(limited: Demo) -> None:
    demo = limited
    for _ in range(SEARCH_USER.count):
        r = demo.client.get(f"{BASE}/search", headers=demo.suresh, params={"q": "QP"})
        assert r.status_code == 200, r.text
    refused = demo.client.get(f"{BASE}/search", headers=demo.suresh, params={"q": "QP"})
    assert refused.status_code == 429 and refused.headers["Retry-After"]
    # Another SME has their own count.
    assert (
        demo.client.get(f"{BASE}/search", headers=demo.anita, params={"q": "QP"}).status_code == 200
    )
