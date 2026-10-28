"""P24: every published operation, called by the admin, an SME and the Governor of the demo
commerce college with its own ids. The table of what each needs is ``tarn_api.persona_sweep``;
it must agree with the core's capability table and leave nothing out."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tarn_adapters.config import Settings
from tarn_adapters.stages import LocalStageRunner
from tarn_adapters.testing import scripted_stages
from tarn_api import persona_sweep
from tarn_api.app import create_app
from tarn_api.persona_sweep import ACCESS, MARKS_AND_TEXT, PERSONAS, checked, unclassified
from tarn_api.security import ROLE_REFUSED
from tarn_api.tenancy_sweep import A_INSTITUTION, World, build_world, login
from tarn_api.testing import MemoryBackends
from tarn_core.domain.personas import CAPABILITIES, Capability
from tarn_core.domain.tenancy import Role
from tarn_core.ids import BookletId, CollegeId
from tarn_core.seed import DEV_PASSWORD
from tarn_core.testing.auth_world import AuthWorld
from tarn_core.testing.seed_world import seed_in_memory

GOVERNOR = "governor@demo-com.example.test"


@pytest.fixture(scope="module")
def sweep() -> Iterator[tuple[World, dict[str, dict[str, str]]]]:
    backends = MemoryBackends()
    mem = backends.mem
    seed_in_memory(mem)
    tenant = mem.identity.find_tenant(A_INSTITUTION)
    assert tenant is not None
    world = AuthWorld(mem)
    world.accounts.invite_governor(
        tenant.college_id, display_name="Demo Governor", email=GOVERNOR, operator="sweep"
    )
    world.auth.accept_invite(tenant.college_id, mem.mailer.last_token(GOVERNOR), DEV_PASSWORD)
    app = create_app(
        Settings(_env_file=None, device="cpu", rate_limits_enabled=False), backends=backends
    )

    def process() -> None:
        runner = LocalStageRunner(scripted_stages(mem.blobs), mem)
        assert runner.run(CollegeId(UUID(int=0)), BookletId(UUID(int=0))) == []

    with TestClient(app) as client:
        w = build_world(client, process)
        personas = {
            "admin": w.a_admin,
            "sme": w.a,
            "governor": login(client, A_INSTITUTION, GOVERNOR),
        }
        yield w, personas


def test_every_published_operation_is_classified(
    sweep: tuple[World, dict[str, dict[str, str]]],
) -> None:
    world, _ = sweep
    assert unclassified(world.client.get("/openapi.json").json()) == []


def test_the_sweep_table_is_the_core_capability_table() -> None:
    """Every capability is checked by some route, and each persona's routes are exactly the
    ones its capabilities open."""
    used = {c for access in ACCESS.values() if isinstance(access, frozenset) for c in access}
    assert used == set(Capability)
    assert set(CAPABILITIES) == set(Role)
    assert CAPABILITIES[Role.GOVERNOR] == frozenset({Capability.FREEZE_UNITS})  # P28


def test_the_admin_is_refused_every_marks_text_review_and_sheet_route() -> None:
    admin = CAPABILITIES[Role.ADMIN]
    for op in MARKS_AND_TEXT:
        access = ACCESS[op]
        assert isinstance(access, frozenset) and not access & admin, op
    names = {path for _, path in MARKS_AND_TEXT}
    for needed in ("/review", "/result-sheets", "/pages/{number}/text", "/evaluated-booklets"):
        assert any(needed in path for path in names), needed


@pytest.mark.parametrize("persona", PERSONAS)
def test_every_persona_signs_in_and_reads_itself(
    sweep: tuple[World, dict[str, dict[str, str]]], persona: str
) -> None:
    world, personas = sweep
    me = world.client.get("/api/v1/auth/me", headers=personas[persona])
    assert me.status_code == 200
    role = Role(me.json()["user"]["role"])
    assert role.value == persona
    assert sorted(me.json()["capabilities"]) == sorted(c.value for c in CAPABILITIES[role])


@pytest.mark.parametrize(("method", "path"), checked(), ids=lambda v: str(v))
def test_each_persona_is_allowed_or_refused_as_the_table_says(
    sweep: tuple[World, dict[str, dict[str, str]]], method: str, path: str
) -> None:
    world, personas = sweep
    access = ACCESS[(method, path)]
    assert isinstance(access, frozenset)
    for persona in PERSONAS:
        allowed = bool(access & CAPABILITIES[Role(persona)])
        response = persona_sweep.call(world, personas[persona], method, path)
        if allowed:
            assert response.status_code not in (401, 403), (persona, method, path, response.text)
        else:
            assert response.status_code == 403, (persona, method, path, response.text)
            assert response.json() == {"detail": ROLE_REFUSED}
