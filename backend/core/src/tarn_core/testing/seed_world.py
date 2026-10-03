"""Run the development seed on the in-memory adapters (tests only)."""

from dataclasses import dataclass

from tarn_core.ids import CollegeId
from tarn_core.seed import (
    COLLEGES,
    DEV_PASSWORD,
    SeededAccounts,
    SeededContent,
    SeedReport,
    seed_accounts,
    seed_content,
)
from tarn_core.seed.model import CollegeSeed
from tarn_core.testing import InMemory
from tarn_core.testing.builders import make_services

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
"""Stands in for a reference diagram: the question bank reads the type from the first bytes."""


@dataclass(frozen=True, slots=True)
class SeededCollege:
    seed: CollegeSeed
    accounts: SeededAccounts
    content: SeededContent


def seed_in_memory(mem: InMemory, report: SeedReport | None = None) -> list[SeededCollege]:
    """What ``tarn seed`` does, on ``mem``: safe to call again."""
    report = report if report is not None else SeedReport()
    services = make_services(mem)
    seeded: list[SeededCollege] = []
    for college in COLLEGES:
        tenant = mem.identity.find_tenant(college.institution_id)
        college_id = tenant.college_id if tenant else CollegeId(mem.ids.new())
        accounts = seed_accounts(
            college,
            college_id=college_id,
            tenant=tenant,
            password=DEV_PASSWORD,
            identity=mem.identity,
            users=mem.users,
            colleges=mem.colleges,
            students=mem.students,
            kit=mem.auth_kit,
            runtime=mem.runtime,
            report=report,
        )
        content = seed_content(
            college,
            college_id=college_id,
            question_author=accounts.teacher_ids[0],
            paper_author=accounts.teacher_ids[1],
            content=mem.content,
            bank=services.bank,
            subjects=services.subjects,
            blueprints=services.blueprints,
            assets=lambda _name: PNG,
            report=report,
        )
        seeded.append(SeededCollege(college, accounts, content))
    return seeded
