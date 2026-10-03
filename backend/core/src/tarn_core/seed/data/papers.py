"""The exam blueprints of the sample papers, as public blueprint documents (schema 1.0).

``question_id`` values are ``@CODE`` references to the seeded questions; the seeder replaces
them with the real ids and fills ``subject_id`` before the document goes through
``BlueprintService``. Section rules are the papers' own (requirements "Facts from question
papers"): QP-CI any 5 of 7 x 2, any 4 of 7 x 5, any 2 of 3 x 10; QP-IPR any 5 of 7 x 3, any
3 of 4 x 10, and question 12 OR 13 (a 10 + b 5)."""

from tarn_core.seed.model import PaperSeed

type Doc = dict[str, object]


def _q(code: str, label: str, marks: int) -> Doc:
    return {"type": "question", "label": label, "marks": marks, "question_id": f"@{code}"}


def _qs(prefix: str, first: int, last: int, marks: int) -> list[Doc]:
    return [_q(f"{prefix}-Q{n}", str(n), marks) for n in range(first, last + 1)]


def _alternative(prefix: str, number: int) -> Doc:
    return {
        "label": str(number),
        "marks": 15,
        "question_id": None,
        "parts": [
            {"label": "a", "marks": 10, "question_id": f"@{prefix}-Q{number}A"},
            {"label": "b", "marks": 5, "question_id": f"@{prefix}-Q{number}B"},
        ],
    }


def _document(
    title: str, course_code: str, minutes: int | None, total: int, sections: list[Doc]
) -> Doc:
    return {
        "schema_version": "1.0",
        "title": title,
        "course_code": course_code,
        "subject_id": "@subject",
        "duration_minutes": minutes,
        "total_marks": total,
        "negative_marking": 0,
        "mark_step": 0.5,
        "sections": sections,
    }


QP_CI_TITLE = "QP-CI Constitution of India and Human Rights, June 2021"
QP_IPR_TITLE = "QP-IPR Intellectual Property Rights, July 2021"
ASSIGNMENT_1_TITLE = "Assignment 1 Indian Ethos and Leadership"
ASSIGNMENT_2_TITLE = "Assignment 2 Startup schemes"

QP_CI = PaperSeed(
    title=QP_CI_TITLE,
    subject_code="19AU0003",
    document=_document(
        QP_CI_TITLE,
        "19AU0003",
        180,
        50,
        [
            {
                "label": "A",
                "title": "Short answers (any five)",
                "method": "keyword_formula",
                "choice": {"rule": "any", "n": 5},
                "items": _qs("QP-CI", 1, 7, 2),
            },
            {
                "label": "B",
                "title": "Short essays (any four)",
                "method": "semantic_rubric",
                "choice": {"rule": "any", "n": 4},
                "items": _qs("QP-CI", 8, 14, 5),
            },
            {
                "label": "C",
                "title": "Essays (any two)",
                "method": "semantic_rubric",
                "choice": {"rule": "any", "n": 2},
                "items": _qs("QP-CI", 15, 17, 10),
            },
        ],
    ),
)

QP_IPR = PaperSeed(
    title=QP_IPR_TITLE,
    subject_code="17NC301",
    document=_document(
        QP_IPR_TITLE,
        "17NC301",
        180,
        60,
        [
            {
                "label": "A",
                "title": "Short answers (any five)",
                "method": "keyword_formula",
                "choice": {"rule": "any", "n": 5},
                "items": _qs("QP-IPR", 1, 7, 3),
            },
            {
                "label": "B",
                "title": "Essays (any three)",
                "method": "semantic_rubric",
                "choice": {"rule": "any", "n": 3},
                "items": _qs("QP-IPR", 8, 11, 10),
            },
            {
                "label": "C",
                "title": "Descriptive answer (question 12 or 13)",
                "method": "semantic_rubric",
                "choice": {"rule": "all"},
                "items": [
                    {
                        "type": "or",
                        "alternatives": [
                            _alternative("QP-IPR", 12),
                            _alternative("QP-IPR", 13),
                        ],
                    }
                ],
            },
        ],
    ),
)

ASSIGNMENT_1 = PaperSeed(
    title=ASSIGNMENT_1_TITLE,
    subject_code="IEL",
    document=_document(
        ASSIGNMENT_1_TITLE,
        "IEL",
        None,
        20,
        [
            {
                "label": "A",
                "title": "Assignment questions (all)",
                "method": "semantic_rubric",
                "choice": {"rule": "all"},
                "items": [_q("A1-Q1", "1", 10), _q("A1-Q2", "2", 10)],
            }
        ],
    ),
)

ASSIGNMENT_2 = PaperSeed(
    title=ASSIGNMENT_2_TITLE,
    subject_code="STARTUP-A2",
    document=_document(
        ASSIGNMENT_2_TITLE,
        "STARTUP-A2",
        None,
        10,
        [
            {
                "label": "A",
                "title": "Essay",
                "method": "semantic_rubric",
                "choice": {"rule": "all"},
                "items": [_q("A2-Q1", "1", 10)],
            }
        ],
    ),
)

PAPERS_COMMERCE = (QP_CI, QP_IPR, ASSIGNMENT_1, ASSIGNMENT_2)
