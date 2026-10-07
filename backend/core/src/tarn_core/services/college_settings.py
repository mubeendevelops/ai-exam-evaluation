"""Settings of a college that only a Tarn operator changes (CLI, like tenant approval)."""

from dataclasses import replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.tenancy import College
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId
from tarn_core.ports.repositories import CollegeRepository
from tarn_core.services._support import Runtime


class CollegeSettings:
    def __init__(self, *, colleges: CollegeRepository, runtime: Runtime) -> None:
        self._colleges = colleges
        self._rt = runtime

    def set_llm_scoring(self, college_id: CollegeId, enabled: bool, *, operator: str) -> College:
        """Switch the LLM scorer on or off for a college (P19). On means answer text goes to the
        LLM provider for a second opinion; the audit log names the operator."""
        if not operator.strip():
            raise InvariantError("name the operator who changes the setting")
        college = self._colleges.get(college_id)
        if college.llm_scoring == enabled:
            return college
        updated = replace(college, llm_scoring=enabled)
        self._colleges.save(updated)
        self._rt.record(
            college_id,
            None,
            AuditAction.COLLEGE_LLM_SCORING_SET,
            before={"llm_scoring": college.llm_scoring},
            after={"llm_scoring": enabled, "operator": operator.strip()},
        )
        return updated
