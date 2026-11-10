"""The SME's global search (P37, D228, D243).

One call over what the SME reaches now: their own units (code, paper title, exam code, year),
the students of those units (roll number, name), the papers and schemas of their subjects
(title, exam code, descriptor) and the questions of their subjects (code, text, topic, key).
At most ``GROUP_MAX`` per group. The unit filter is the units the SME owns (``Reach.units``),
the content filter their subjects (``Reach.subjects``): another SME's units and another
subject's content are never searched. The text is never logged or audited."""

from tarn_core.domain.blueprint import BlueprintKind, BlueprintQuery, BlueprintSummary
from tarn_core.domain.personas import Capability
from tarn_core.domain.search import (
    GROUP_MAX,
    BlueprintHit,
    QuestionHit,
    SearchResults,
    UnitHit,
    search_text,
)
from tarn_core.errors import NotFoundError
from tarn_core.ids import CollegeId, UserId
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    EntitlementRepository,
    QuestionQuery,
    UnitRepository,
    UserRepository,
)
from tarn_core.services.access import reach_of, require
from tarn_core.services.unit_flow import paper_summary

QUESTION_TEXT_MAX = 120


def _first_line(text: str) -> str:
    line = next((x.strip() for x in text.splitlines() if x.strip()), "")
    return line if len(line) <= QUESTION_TEXT_MAX else line[: QUESTION_TEXT_MAX - 1] + "…"


def _blueprint(b: BlueprintSummary) -> BlueprintHit:
    return BlueprintHit(
        blueprint_id=b.id,
        title=b.title,
        exam_code=b.exam_code,
        descriptor=b.descriptor,
        subject_id=b.subject_id,
    )


class SearchService:
    def __init__(
        self,
        *,
        users: UserRepository,
        entitlements: EntitlementRepository,
        units: UnitRepository,
        content: ContentRepository,
        booklets: BookletRepository,
    ) -> None:
        self._users = users
        self._entitlements = entitlements
        self._units = units
        self._content = content
        self._booklets = booklets

    def search(self, college_id: CollegeId, actor_id: UserId, raw: str) -> SearchResults:
        """``PermissionDeniedError`` for anyone but an SME; ``InvariantError`` for a query of
        fewer than 2 or more than 80 characters."""
        user = require(self._users.get(college_id, actor_id), Capability.VIEW_OWN_WORK)
        text = search_text(raw)
        needle = text.casefold()
        reach = reach_of(user, self._entitlements, self._units)
        subjects = reach.subjects if reach.subjects is not None else frozenset()
        owned = reach.units if reach.units is not None else frozenset()

        units: list[UnitHit] = []
        for unit in sorted(
            (u for u in self._units.list(college_id, owner_id=user.id) if u.id in owned),
            key=lambda u: (u.due_date, u.code),
        ):
            try:
                paper = paper_summary(
                    self._content, self._units, college_id, unit.id, unit.paper_id
                )
                title, exam_code = paper.title, paper.exam_code
            except NotFoundError:
                title, exam_code = "", ""
            if any(needle in f.casefold() for f in (unit.code, title, exam_code, unit.year)):
                units.append(
                    UnitHit(
                        unit_id=unit.id,
                        code=unit.code,
                        paper_title=title,
                        exam_code=exam_code,
                        year=unit.year,
                        due_date=unit.due_date,
                        state=unit.state.value,
                    )
                )
            if len(units) == GROUP_MAX:
                break

        students = (
            self._booklets.find_students(college_id, owned, text, limit=GROUP_MAX) if owned else ()
        )

        def blueprints(kind: BlueprintKind) -> tuple[BlueprintHit, ...]:
            if not subjects:
                return ()
            page = self._content.blueprint_page(
                college_id,
                BlueprintQuery(kind=kind, subject_ids=subjects, search=text),
                limit=GROUP_MAX,
            )
            return tuple(_blueprint(b) for b in page.items)

        questions: tuple[QuestionHit, ...] = ()
        if subjects:
            found = self._content.search_questions(
                college_id, QuestionQuery(keyword=text, subject_ids=subjects), limit=GROUP_MAX
            )
            questions = tuple(
                QuestionHit(
                    question_id=q.id,
                    code=q.code,
                    text=_first_line(q.text),
                    subject_id=q.subject_id,
                )
                for q in found.items
            )
        return SearchResults(
            units=tuple(units),
            students=tuple(students),
            papers=blueprints(BlueprintKind.PAPER),
            schemas=blueprints(BlueprintKind.SCHEMA),
            questions=questions,
        )


__all__ = ["SearchService"]
