"""Request and response bodies (the OpenAPI contract, ``docs/api/openapi.json``)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tarn_core.domain.tenancy import User

Email = Field(min_length=3, max_length=320, examples=["evaluator@institution.edu"])
Password = Field(min_length=1, max_length=1024)
DiagramKindName = Literal[
    "flowchart", "block", "network", "tree", "circuit", "plot", "labelled_drawing"
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class ErrorOut(BaseModel):
    detail: str


class PolicyErrorOut(BaseModel):
    detail: str
    reasons: list[str]


# --- sign-in ----------------------------------------------------------------------------------


class LoginIn(_In):
    institution_id: str = Field(
        min_length=1,
        max_length=64,
        description="Institution / Org Domain ID; compared upper-cased.",
        examples=["TARN_INST_01"],
    )
    email: str = Email
    password: str = Password
    remember: bool = Field(False, description="Remember session: a persistent, longer session.")


class UserOut(BaseModel):
    id: UUID
    college_id: UUID
    display_name: str
    email: str
    role: Literal["admin", "teacher"]
    active: bool

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            college_id=user.college_id,
            display_name=user.display_name,
            email=user.email,
            role=user.role.value,
            active=user.active,
        )


class TokenOut(BaseModel):
    status: Literal["signed_in"] = "signed_in"
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (OAuth2 token type)
    expires_in: int = Field(description="Seconds until the access token expires.")
    user: UserOut


class ResetRequiredOut(BaseModel):
    """The password is right, but an administrator requires a new one first."""

    status: Literal["reset_required"] = "reset_required"
    reset_token: str = Field(description="Use with POST /api/v1/auth/password/reset.")


class MeOut(BaseModel):
    user: UserOut
    institution_id: str
    college_name: str
    recovery_codes_left: int


# --- passwords --------------------------------------------------------------------------------


class ForgotIn(_In):
    institution_id: str = Field(min_length=1, max_length=64)
    email: str = Email


class ResetIn(_In):
    token: str = Field(min_length=10, max_length=256)
    new_password: str = Password


class RecoverIn(_In):
    institution_id: str = Field(min_length=1, max_length=64)
    email: str = Email
    recovery_code: str = Field(min_length=16, max_length=64, examples=["ABCD-EFGH-IJKL-MNOP"])
    new_password: str = Password


class ChangePasswordIn(_In):
    current_password: str = Password
    new_password: str = Password


class CurrentPasswordIn(_In):
    current_password: str = Password


class RecoveryCodesOut(BaseModel):
    codes: list[str] = Field(description="Shown once. Only their hashes are stored.")


class InviteAcceptIn(_In):
    token: str = Field(min_length=10, max_length=256)
    password: str = Password


# --- registration -----------------------------------------------------------------------------


class AvailabilityOut(BaseModel):
    institution_id: str
    valid: bool
    available: bool
    problem: str | None = None


class RegistrationIn(_In):
    institution_id: str = Field(
        min_length=1,
        max_length=20,
        description="Upper-case letters, digits and _ - * &; at most 20; no spaces.",
        examples=["TARN_INST_01&*"],
    )
    institution_name: str | None = Field(None, max_length=200)
    admin_name: str = Field(min_length=1, max_length=200, examples=["Dr. Sarah Connor"])
    email: str = Email
    password: str = Password


class RegistrationOut(BaseModel):
    institution_id: str
    status: str
    approval_required: bool


class VerifyEmailIn(_In):
    token: str = Field(min_length=10, max_length=256)


class TenantStatusOut(BaseModel):
    institution_id: str
    status: str


# --- accounts ---------------------------------------------------------------------------------


class AccountOut(BaseModel):
    user: UserOut
    status: Literal["PENDING", "ACTIVE", "DISABLED"]
    locked_until: datetime | None
    force_reset: bool


class InviteIn(_In):
    display_name: str = Field(min_length=1, max_length=200)
    email: str = Email


# --- roster -----------------------------------------------------------------------------------


class RowErrorOut(BaseModel):
    line: int
    field: str
    message: str


class RosterReportOut(BaseModel):
    imported: bool = Field(description="False if any row has an error: then nothing is written.")
    rows: int
    created: int
    updated: int
    unchanged: int
    errors: list[RowErrorOut]


class StudentOut(BaseModel):
    id: UUID
    name: str
    usn: str
    class_section: str


# --- subjects and exam blueprints -------------------------------------------------------------


class SubjectIn(_In):
    code: str = Field(min_length=1, max_length=40, examples=["PHY-501"])
    name: str = Field(min_length=1, max_length=200, examples=["Physics"])


class SubjectOut(BaseModel):
    id: UUID
    code: str
    name: str
    owning_college_id: UUID
    owned: bool = Field(description="True if my college owns it.")


class IssueOut(BaseModel):
    path: str = Field(description="Where in the document, e.g. sections[1].items[3].marks.")
    message: str


class SectionSummaryOut(BaseModel):
    label: str
    method: str
    items: int = Field(description="M in 'answer any N of M'.")
    counted: int = Field(description="N: how many items count towards the total.")
    max_marks: float = Field(description="Most this section can earn: the N largest items.")


class ValidationOut(BaseModel):
    valid: bool
    issues: list[IssueOut]
    warnings: list[IssueOut]
    sections: list[SectionSummaryOut] = Field(
        description="Empty if the document is too broken to read its sections."
    )
    computed_total: float | None = Field(
        description="Sum of the section maximums with the choice rules applied; null if unknown."
    )
    question_count: int
    unlinked: list[str] = Field(
        description="Leaf labels (7, 12.a) not linked to a question yet. Allowed in a blueprint, "
        "but booklets can only be registered once this is empty."
    )


class ContentRefOut(BaseModel):
    kind: str
    id: UUID
    version: int


class BlueprintSummaryOut(BaseModel):
    id: UUID
    version: int
    title: str
    course_code: str
    subject_id: UUID
    subject_name: str | None
    total_marks: float
    duration_minutes: int | None
    section_count: int
    question_count: int
    unlinked_count: int
    owning_college_id: UUID
    owned: bool = Field(description="True if my college owns it and so may edit it.")
    copied_from: ContentRefOut | None


class BlueprintOut(BlueprintSummaryOut):
    document: dict[str, Any] = Field(
        description="The blueprint in the public form, see docs/api/blueprint.schema.json."
    )


# --- question bank ----------------------------------------------------------------------------

DifficultyName = Literal["easy", "medium", "hard"]


class ListItemBody(_In):
    term: str = Field(min_length=1, max_length=200)
    synonyms: list[str] = Field(default_factory=list, max_length=50)


class ListParamsBody(_In):
    items: list[ListItemBody] = Field(min_length=1, max_length=100)
    required_count: int = Field(ge=1, description="How many items the student must name.")


class NumericParamsBody(_In):
    expected: float
    tolerance: float = Field(0, ge=0)
    unit: str = Field("", max_length=40)


class SemanticParamsBody(_In):
    reference_statement: str = Field(min_length=1, max_length=2000)


class DiagramParamsBody(_In):
    reference_diagram_id: UUID
    component: Literal["whole", "nodes", "edges", "labels"] = "whole"


class _CriterionBase(_In):
    id: UUID | None = Field(
        None, description="Omit for a new criterion; give it to save a new version of one."
    )
    label: str = Field(min_length=1, max_length=200)
    weight: float = Field(gt=0, description="In marks. All weights add up to the question's marks.")


class ListCriterion(_CriterionBase):
    type: Literal["list"]
    params: ListParamsBody


class NumericCriterion(_CriterionBase):
    type: Literal["numeric"]
    params: NumericParamsBody


class SemanticCriterion(_CriterionBase):
    type: Literal["semantic"]
    params: SemanticParamsBody


class DiagramCriterion(_CriterionBase):
    type: Literal["diagram"]
    params: DiagramParamsBody


CriterionBody = Annotated[
    ListCriterion | NumericCriterion | SemanticCriterion | DiagramCriterion,
    Field(discriminator="type"),
]


class QuestionCreateIn(_In):
    subject_id: UUID
    code: str = Field(min_length=1, max_length=40, examples=["PHY-Q101"])
    text: str = Field(min_length=1, max_length=5000)
    max_marks: float = Field(gt=0)
    difficulty: DifficultyName = "medium"
    category: str = Field("", max_length=200, description="The topic, e.g. Electromagnetism.")
    reference_answer: str | None = Field(None, max_length=20000)
    criteria: list[CriterionBody] = Field(default_factory=list, max_length=50)


class QuestionUpdateIn(_In):
    code: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=5000)
    max_marks: float = Field(gt=0)
    difficulty: DifficultyName
    category: str = Field("", max_length=200)
    criteria: list[CriterionBody] | None = Field(
        None,
        max_length=50,
        description="Replaces the rubric. Required when the marks change and a rubric exists.",
    )


class RubricIn(_In):
    criteria: list[CriterionBody] = Field(max_length=50)


class ReferenceAnswerIn(_In):
    text: str = Field(min_length=1, max_length=20000)
    guidance_only: bool = Field(
        False, description="The key is guidance only: no AI score, the teacher marks by hand (C8)."
    )


class GlossaryIn(_In):
    terms: list[str] = Field(max_length=500)


class QuestionSummaryOut(BaseModel):
    id: UUID
    version: int
    code: str
    text: str
    max_marks: float
    difficulty: DifficultyName
    category: str
    subject_id: UUID
    subject_name: str | None
    key_count: int = Field(description="Reference answers plus attached key files.")
    owning_college_id: UUID
    owner_name: str | None
    owned: bool = Field(description="True if my college owns it and so may edit it.")
    copied_from: ContentRefOut | None


class QuestionPageOut(BaseModel):
    items: list[QuestionSummaryOut]
    total: int
    limit: int
    offset: int


class ReferenceAnswerOut(BaseModel):
    id: UUID
    version: int
    text: str
    guidance_only: bool
    synthetic: bool


class CriterionOut(BaseModel):
    """A criterion as stored: the same shape the editor sends, with its id and version."""

    version: int
    criterion: CriterionBody


class RubricOut(BaseModel):
    criteria: list[CriterionOut]
    total: float = Field(description="Sum of the weights.")
    max_marks: float
    complete: bool = Field(description="True if the weights add up to the question's marks.")


class GlossaryOut(BaseModel):
    teacher_terms: list[str]
    reference_labels: list[str] = Field(description="Labels read from the reference diagrams.")
    terms: list[str] = Field(description="Both lists together, case-insensitively de-duplicated.")


class KeyFileOut(BaseModel):
    id: UUID
    name: str
    media_type: str
    size_bytes: int
    keywords: list[str]
    content_url: str


class ReferenceDiagramOut(BaseModel):
    id: UUID
    name: str
    node_count: int
    edge_count: int
    labels: list[str]
    content_url: str
    kind: DiagramKindName
    recognition: Literal["pending", "recognised", "failed", "edited"] = Field(
        description="pending: the worker has not read the PNG yet; failed: draw it by hand."
    )
    version: int
    graph_url: str


class QuestionOut(QuestionSummaryOut):
    reference_answers: list[ReferenceAnswerOut]
    rubric: RubricOut
    glossary: GlossaryOut
    key_files: list[KeyFileOut]
    diagrams: list[ReferenceDiagramOut]


# --- booklets: upload, page cleaning, status ----------------------------------------------------

BookletStatusName = Literal[
    "uploaded",
    "processing",
    "needs_retake",
    "pages_ready",
    "reading",
    "text_ready",
    "segmented",
    "failed",
    "scored",
    "in_review",
    "approved",
    "amendment_in_progress",
    "approved_amended",
]
RetakeReasonName = Literal["blurry", "glare", "low_resolution", "no_page_found"]


class BookletStudentOut(BaseModel):
    id: UUID
    name: str
    usn: str


class BookletBlueprintOut(BaseModel):
    id: UUID
    version: int
    title: str


class PageOut(BaseModel):
    number: int = Field(description="Page number in upload order, from 1.")
    cleaned: bool
    width: int
    height: int
    retake_reasons: list[RetakeReasonName] = Field(
        description="Why the quality gate asks for a retake; empty when the page passed."
    )
    use_anyway: bool = Field(description="The teacher chose to go on with a flagged page.")
    sharpness: float | None = Field(
        description="Edge strength of the writing; null when the page has too little writing."
    )
    glare_share: float | None
    rotation_degrees: int | None = Field(description="Clockwise quarter turn applied to the page.")
    rotation_guessed: bool | None = Field(
        description="The direction of that turn is a default, not a finding."
    )
    skew_degrees: float | None
    cropped: bool | None
    perspective_corrected: bool | None
    neighbour_removed: bool | None = Field(description="A neighbouring page was cut away.")
    page_found: bool | None
    image_url: str | None = Field(description="The cleaned page (needs the bearer token).")
    original_url: str | None
    text_read: bool = Field(description="OCR has read this page (P10).")
    needs_text: bool = Field(
        description="Every OCR engine failed on this page: the teacher types it or retakes it."
    )
    ocr_failures: list[str] = Field(
        description="Engines that failed on this page, as `engine:timeout` or `engine:error`."
    )
    text_url: str | None = Field(description="The page's lines and readings, once read.")


class BookletOut(BaseModel):
    id: UUID
    status: BookletStatusName
    student: BookletStudentOut
    blueprint: BookletBlueprintOut
    uploaded_by: UUID
    uploaded_at: datetime
    version: int
    page_count: int = Field(description="0 until the file has been split into pages.")
    pages_cleaned: int
    flagged_pages: list[int] = Field(
        description="Numbers of the pages that still need a retake or a 'use anyway'."
    )
    pages_read: int = Field(description="Pages OCR has read.")
    needs_text_pages: list[int] = Field(
        description="Numbers of the pages no OCR engine could read."
    )
    failure_reason: (
        Literal[
            "unreadable_file",
            "too_many_pages",
            "processing_failed",
            "reading_failed",
            "segmentation_failed",
            "diagrams_failed",
            "scoring_failed",
        ]
        | None
    )
    duplicate_of: list[UUID] = Field(
        description="Other booklets of this college with the same file (set on upload only)."
    )


class BookletDetailOut(BookletOut):
    pages: list[PageOut]


ContentClassName = Literal["print", "cursive", "numeric"]


class ReadingOut(BaseModel):
    engine: str
    engine_version: str
    text: str
    confidence: float = Field(description="The engine's raw confidence, 0..1.")
    box: list[int] = Field(description="x0, y0, x1, y1 in page pixels.")
    calibrated: float | None = Field(description="p̂: the confidence through the calibration.")
    agreement: float | None
    lexicon: float | None
    weight: float | None
    score: float | None = Field(description="S = weight·p̂ + α·agreement + β·lexicon.")
    competing: bool | None = Field(
        description="False when the engine is not in the line's class set (kept, never chosen)."
    )


class RegionOut(BaseModel):
    id: UUID
    kind: Literal["text_line", "text_block", "diagram", "table", "label"]
    box: list[int]
    text: str | None = Field(description="The teacher's text if corrected, else the chosen one.")
    chosen: int | None = Field(description="Index of the chosen reading.")
    content_class: ContentClassName | None
    line_score: float | None = Field(description="Winner's S over the best S possible, 0..1.")
    flagged: bool = Field(description="Below the line threshold: highlighted for the teacher.")
    struck_out: bool = Field(description="Left out of scoring (set by the teacher).")
    read_by: list[str]
    parent_id: UUID | None = Field(description="The table a cell belongs to.")
    row: int | None
    col: int | None
    readings: list[ReadingOut]


class PageTextOut(BaseModel):
    number: int
    text_read: bool
    needs_text: bool
    ocr_failures: list[str]
    regions: list[RegionOut] = Field(description="In reading order; table cells after their table.")


class BookletPageOut(BaseModel):
    items: list[BookletOut]
    total: int
    limit: int
    offset: int
    waiting: int = Field(description="Your booklets that are queued or being processed.")
    max_waiting: int = Field(description="How many you may have waiting at once.")


# --- diagrams (P14) ---------------------------------------------------------------------------

ShapeName = Literal["terminal", "process", "decision", "io", "circle", "block", "other"]
EditOpName = Literal[
    "add_node",
    "remove_node",
    "relabel_node",
    "reshape_node",
    "add_edge",
    "remove_edge",
    "relabel_edge",
    "reverse_edge",
    "set_edge_ends",
]


class GraphNodeOut(BaseModel):
    id: str
    shape: ShapeName
    label: str
    box: list[int] | None = Field(description="[x0, y0, x1, y1] in the image's pixels.")
    confidence: float = Field(description="The recognizer's confidence in the shape, 0..1.")
    label_confidence: float | None = Field(description="OCR line score of the label, 0..1.")


class GraphEdgeOut(BaseModel):
    id: str
    source: str | None = Field(description="The node at the tail; null: touches no shape.")
    target: str | None = Field(description="The node at the head; null: touches no shape.")
    label: str
    directed: bool = Field(description="false: a line without an arrow head.")
    confidence: float
    box: list[int] | None
    tail: list[int] | None = Field(description="[x, y]")
    head: list[int] | None = Field(description="[x, y]")


class FreeLabelOut(BaseModel):
    text: str
    box: list[int] | None
    confidence: float | None


class EngineOut(BaseModel):
    name: str
    version: str


class DiagramGraphOut(BaseModel):
    """``docs/api/diagram-graph.schema.json`` v1.0."""

    schema_version: Literal["1.0"]
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    free_labels: list[FreeLabelOut]
    recognizer: EngineOut | None = Field(description="null: drawn by the teacher.")
    label_engines: list[str]
    edited_by_teacher: bool


class ReferenceGraphOut(BaseModel):
    diagram_id: UUID
    version: int = Field(description="Send it back as expected_version when editing.")
    kind: DiagramKindName
    recognition: Literal["pending", "recognised", "failed", "edited"]
    graph: DiagramGraphOut
    content_url: str


class GraphEditIn(_In):
    op: EditOpName
    id: str | None = Field(
        None, max_length=64, description="The node or edge; for an add, the new id or null."
    )
    shape: ShapeName | None = None
    label: str | None = Field(None, max_length=200)
    source: str | None = Field(None, max_length=64)
    target: str | None = Field(None, max_length=64)
    directed: bool | None = None
    box: list[Annotated[int, Field(ge=0)]] | None = Field(None, min_length=4, max_length=4)


class ReferenceGraphEditIn(_In):
    expected_version: int = Field(ge=1)
    edits: list[GraphEditIn] = Field(default_factory=list, max_length=500)
    kind: DiagramKindName | None = Field(None, description="Change what the diagram is.")


class StudentDiagramOut(BaseModel):
    id: UUID
    segment_id: UUID
    region_id: UUID | None
    version: int = Field(description="Send it back as expected_version when editing.")
    kind: DiagramKindName
    box: list[int] = Field(description="Where it is on its page, in page pixels.")
    graph: DiagramGraphOut


class StudentGraphEditIn(_In):
    expected_version: int = Field(ge=1)
    edits: list[GraphEditIn] = Field(min_length=1, max_length=500)


class DiagramComparisonOut(BaseModel):
    criterion_id: UUID
    criterion_version: int
    credit: float
    similarity: float | None
    flags: list[str]
    document: dict[str, Any] = Field(
        description="The R6 result, as docs/api/diagram-comparison.schema.json v1.0 describes."
    )


# --- the review (P15) ---------------------------------------------------------------------------

AnswerStatusName = Literal["suggested", "skipped", "approved"]


class LockOut(BaseModel):
    holder_id: UUID
    holder_name: str
    acquired_at: datetime
    expires_at: datetime = Field(
        description="Lapses then unless the holder writes or refreshes (POST .../lock) first."
    )
    mine: bool = Field(description="The caller holds it.")


class LockedOut(BaseModel):
    detail: str
    holder_id: UUID | None = Field(
        description="Who has the booklet open (GET .../review names them)."
    )
    expires_at: datetime | None


class CriterionResultOut(BaseModel):
    criterion_id: UUID
    criterion_version: int
    weight: float
    credit: float = Field(description="0..1; marks = weight × credit.")
    marks: float
    scorer: str
    flags: list[str] = Field(description="`check`: borderline; `manual`: the teacher marks it.")
    similarity: float | None
    reason: str | None = Field(description="Why this credit (Panel B).")
    matched: list[str]
    missing: list[str]


class SuggestionOut(BaseModel):
    id: UUID
    mark: float | None = Field(description="None: the key is guidance only (mark manually).")
    mark_step: float
    flags: list[str]
    reasons: list[str] = Field(
        description="Answer-level notes, including the notice of a re-score after the key, "
        "rubric, glossary or reference diagram changed."
    )
    relevance: float | None
    created_at: datetime
    criteria: list[CriterionResultOut]


class ApprovalOut(BaseModel):
    id: UUID
    ai_mark: float | None
    teacher_mark: float
    overridden: bool
    tags: list[str]
    remarks: str
    reviewer_id: UUID
    reviewed_at: datetime


class DraftOut(BaseModel):
    amendment_id: UUID
    reason: str
    opened_by: UUID
    opened_at: datetime


class ReviewAnswerOut(BaseModel):
    id: UUID
    slot_label: str
    status: AnswerStatusName
    version: int = Field(description="Send it back as expected_version with a decision.")
    rescore_pending: bool = Field(
        description="A new suggestion is on its way; the answer cannot be approved until then."
    )
    attempted: bool = Field(description="False when segment edits left it without text.")
    max_marks: float
    suggestion: SuggestionOut | None = Field(description="The latest AI suggestion.")
    approval: ApprovalOut | None = Field(
        description="The latest approval (the amended one while the answer is a draft)."
    )
    draft: DraftOut | None = Field(description="Set while the answer is an amendment draft.")


class SlotResultOut(BaseModel):
    section_label: str
    slot_label: str
    mark: float | None
    counted: bool
    outcome: str = Field(
        description="counted, not attempted, not counted: best N, or not counted: other OR "
        "alternative scored higher."
    )


class TotalsOut(BaseModel):
    total: float
    max_marks: float
    slots: list[SlotResultOut]


class ResultSheetOut(BaseModel):
    id: UUID
    version: int
    total: float
    max_marks: float
    issued_by: UUID
    issued_at: datetime
    note: str = Field(description="What this version amended (empty on version 1).")
    lines: list[SlotResultOut]


class ReviewOut(BaseModel):
    booklet_id: UUID
    status: BookletStatusName
    version: int = Field(
        description="Send it back as expected_version when approving the booklet or editing "
        "its text or segments."
    )
    approved: bool = Field(description="A result sheet stands (also during an amendment).")
    amendment_in_progress: bool = Field(description="The badge of an approved booklet.")
    lock: LockOut | None
    can_approve: bool
    waiting: list[str] = Field(description="Questions whose answers still need a decision.")
    answers: list[ReviewAnswerOut]
    totals: TotalsOut = Field(
        description="Teacher marks where approved, AI marks elsewhere, best N and OR applied."
    )
    sheets: list[ResultSheetOut] = Field(description="Every version issued, oldest first.")
    rescoring: list[UUID] = Field(
        default_factory=list,
        description="On open: answers sent for re-scoring because their content changed.",
    )
    notices: list[str] = Field(default_factory=list)


class ExpectedVersionIn(_In):
    expected_version: int = Field(ge=1)


class ApproveAnswerIn(_In):
    expected_version: int = Field(ge=1)
    teacher_mark: float | None = Field(
        None,
        ge=0,
        description="Omit to accept the AI's mark; required when there is none. A multiple of "
        "the paper's mark step, at most the question's marks.",
    )
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=10
    )
    remarks: str = Field("", max_length=2000)


class ReopenIn(_In):
    expected_version: int = Field(ge=1)
    reason: str = Field("", max_length=500, description="Optional (design decision 7).")


class RegionEditIn(_In):
    expected_version: int = Field(ge=1, description="The booklet's version.")
    text: str | None = Field(None, max_length=2000, description="Omit to keep the text.")
    struck_out: bool | None = Field(None, description="Omit to keep the mark.")


class RegionEditOut(BaseModel):
    booklet_version: int
    region: RegionOut
    rescoring: list[UUID] = Field(description="Answers sent for re-scoring.")


class SegmentOut(BaseModel):
    id: UUID
    slot_label: str | None = Field(description="None: the unassigned tray.")
    proposed_label: str | None
    position: int
    source: Literal["rule", "similarity", "teacher"]
    flags: list[str]
    match_score: float | None
    region_ids: list[UUID]
    page_ids: list[UUID]


class SegmentsOut(BaseModel):
    booklet_version: int
    segments: list[SegmentOut]
    rescoring: list[UUID] = Field(default_factory=list)
    emptied: list[UUID] = Field(
        default_factory=list, description="Answers left without text (not attempted)."
    )


class MergeIn(_In):
    expected_version: int = Field(ge=1)
    first: UUID
    second: UUID


class SplitIn(_In):
    expected_version: int = Field(ge=1)
    segment_id: UUID
    at_region: UUID
    label: str | None = Field(None, max_length=32, description="None: the unassigned tray.")


class ReassignIn(_In):
    expected_version: int = Field(ge=1)
    segment_id: UUID
    label: str | None = Field(None, max_length=32, description="None: the unassigned tray.")


class MoveBoundaryIn(_In):
    expected_version: int = Field(ge=1)
    upper: UUID
    lower: UUID
    region: UUID = Field(description="The first region of `lower` after the move.")
