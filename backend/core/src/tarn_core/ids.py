"""Typed identifiers. Each is a ``NewType`` over ``uuid.UUID`` so mypy rejects, say,
a booklet id passed where a college id is expected."""

from typing import NewType
from uuid import UUID

CollegeId = NewType("CollegeId", UUID)
UserId = NewType("UserId", UUID)
StudentId = NewType("StudentId", UUID)

# Global content
SubjectId = NewType("SubjectId", UUID)
QuestionId = NewType("QuestionId", UUID)
ReferenceAnswerId = NewType("ReferenceAnswerId", UUID)
CriterionId = NewType("CriterionId", UUID)
GlossaryId = NewType("GlossaryId", UUID)
ReferenceDiagramId = NewType("ReferenceDiagramId", UUID)
KeyFileId = NewType("KeyFileId", UUID)
BlueprintId = NewType("BlueprintId", UUID)
OcrCalibrationId = NewType("OcrCalibrationId", UUID)
ScoringCalibrationId = NewType("ScoringCalibrationId", UUID)

# College data
BookletId = NewType("BookletId", UUID)
PageId = NewType("PageId", UUID)
RegionId = NewType("RegionId", UUID)
SegmentId = NewType("SegmentId", UUID)
AnswerId = NewType("AnswerId", UUID)
StudentDiagramId = NewType("StudentDiagramId", UUID)
AnswerScoreId = NewType("AnswerScoreId", UUID)
ReviewId = NewType("ReviewId", UUID)
ResultSheetId = NewType("ResultSheetId", UUID)
AuditEventId = NewType("AuditEventId", UUID)
JobId = NewType("JobId", UUID)

# Identity store (separate database, P4)
AuthSessionId = NewType("AuthSessionId", UUID)
