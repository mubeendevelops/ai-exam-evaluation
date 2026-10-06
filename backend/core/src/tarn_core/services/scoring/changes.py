"""What changed in the content an answer is scored against (design.md "Workflow engine":
"Key, rubric or reference diagram changed: unapproved answers re-scored with a notice").

Scores record every content version they used (rule 11), so comparing two scores' versions, or
a score's versions with what the scorer would use now, tells which key, rubric criterion,
glossary, reference diagram or question changed. Calibrations and the blueprint (pinned to the
booklet) are not content changes."""

from collections.abc import Iterable

from tarn_core.domain.common import ContentKind, ContentRef

WATCHED = frozenset(
    {
        ContentKind.QUESTION,
        ContentKind.REFERENCE_ANSWER,
        ContentKind.RUBRIC_CRITERION,
        ContentKind.GLOSSARY,
        ContentKind.REFERENCE_DIAGRAM,
    }
)

_NAMES = {
    ContentKind.QUESTION: "question",
    ContentKind.REFERENCE_ANSWER: "key",
    ContentKind.RUBRIC_CRITERION: "rubric criterion",
    ContentKind.GLOSSARY: "glossary",
    ContentKind.REFERENCE_DIAGRAM: "reference diagram",
}


def _watched(refs: Iterable[ContentRef]) -> dict[tuple[ContentKind, object], int]:
    return {(r.kind, r.id): r.version for r in refs if r.kind in WATCHED}


def content_changes(before: Iterable[ContentRef], after: Iterable[ContentRef]) -> tuple[str, ...]:
    """One phrase per changed item, e.g. ``"rubric criterion v2 → v3"``, ``"key added"``,
    ``"glossary removed"``; empty when nothing the score depends on changed. Ordered by kind,
    then by the phrase."""
    old, new = _watched(before), _watched(after)
    phrases: list[tuple[str, str]] = []
    for key in old.keys() | new.keys():
        kind = key[0]
        name = _NAMES[kind]
        if key not in old:
            phrases.append((kind.value, f"{name} added"))
        elif key not in new:
            phrases.append((kind.value, f"{name} removed"))
        elif old[key] != new[key]:
            phrases.append((kind.value, f"{name} v{old[key]} → v{new[key]}"))
    return tuple(p for _, p in sorted(phrases))


def change_notice(changes: tuple[str, ...]) -> str:
    return "re-scored because the content changed: " + "; ".join(changes)
