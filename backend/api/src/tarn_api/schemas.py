"""Request and response bodies (the OpenAPI contract, ``docs/api/openapi.json``)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tarn_core.domain.tenancy import User

Email = Field(min_length=3, max_length=320, examples=["evaluator@institution.edu"])
Password = Field(min_length=1, max_length=1024)


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
