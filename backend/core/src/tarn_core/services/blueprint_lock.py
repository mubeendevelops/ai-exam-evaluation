"""A paper's structure is locked once booklets exist (D222, P34).

While any booklet is pinned to a version of a paper, a new version may change only its
metadata: the title, descriptor, description, target batch, duration and course code. Sections,
slots, marks, rules, links, the exam code, the subject and the rest are refused; the SME clones
the paper to change them. Questions, keys and rubrics stay editable (D111 re-scores unapproved
answers). Pure: the caller says whether booklets exist."""

from dataclasses import replace

from tarn_core.domain.blueprint import ExamBlueprint

METADATA_FIELDS = (
    "title",
    "descriptor",
    "description",
    "target_batch",
    "duration_minutes",
    "course_code",
)
"""What a locked paper may still change."""

_NAMES = {
    "sections": "sections, questions, marks and links",
    "total_marks": "total marks",
    "mark_step": "mark step",
    "exam_code": "exam code",
    "subject_id": "subject",
    "kind": "kind",
    "weightage_note": "rubric weightage description",
    "from_schema": "schema it was made from",
}

LOCKED_MESSAGE = (
    "Booklets were uploaded against this paper, so its structure is locked: only the title, "
    "descriptor, description, target batch, duration and course code may change; "
    "clone it to change the structure."
)


def structure_changes(old: ExamBlueprint, new: ExamBlueprint) -> tuple[str, ...]:
    """What ``new`` changes beyond the metadata, in words; empty = only metadata changed.
    Identity, version and save time are not compared."""
    metadata = {name: getattr(new, name) for name in METADATA_FIELDS}
    before = replace(old, meta=new.meta, saved_at=new.saved_at, **metadata)
    if before == new:
        return ()
    return tuple(
        words for field, words in _NAMES.items() if getattr(before, field) != getattr(new, field)
    ) or ("structure",)
