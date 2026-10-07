"""``tarn evaluate``: one booklet, end to end, with no API, database or web UI (R1, R2).

The folder (or ``-`` for a byte stream on stdin) holds the booklet's page images or one PDF.
The exam and the roster come from the development seed (two demo colleges, the sample papers
with their synthetic keys) loaded into the core's in-memory adapters for the run; the stages
are the worker's own (``tarn_adapters.stages``), with the real page cleaner and OCR engines
unless the settings say otherwise. The run writes two files:

- ``result.json``: the AI's suggested mark per answer and criterion, the flags, the total.
- ``result-sheet-draft.pdf``: the result sheet, stamped DRAFT.

Both are AI suggestions: a teacher approves every mark in the review (rule 7). The LLM scorer
never runs here and no cloud engine runs unless the settings enable it, exactly as in the
worker; student text is never written to a log. The folder may hold a ``booklet.json`` with
``{"college": "...", "exam": "...", "usn": "..."}``; the options override it."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from tarn_adapters.config import Settings, get_settings
from tarn_adapters.local import build_evaluation
from tarn_adapters.seed.runner import asset_reader
from tarn_adapters.sources import FolderPageSource, StreamPageSource
from tarn_adapters.stages import Stages
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import BookletStatus
from tarn_core.errors import DomainError
from tarn_core.ids import CollegeId, StudentId, UserId
from tarn_core.ports.storage import BlobStore, PageSource
from tarn_core.services.evaluation import Evaluated
from tarn_core.services.uploads import UploadLimits
from tarn_core.testing import InMemory
from tarn_core.testing.seed_world import SeededCollege, seed_in_memory

MANIFEST = "booklet.json"
RESULT_JSON = "result.json"
RESULT_PDF = "result-sheet-draft.pdf"

EXIT_RETAKE = 2
"""Exit code when the quality gate asked for a retake and ``--use-anyway`` was not given."""


def build_stages(settings: Settings, blobs: BlobStore) -> Stages:
    """The worker's stages with the settings' engines, and no LLM scorer (D131)."""
    from tarn_adapters.stages_wiring import build_stages as build

    return build(settings, blobs, llm=None, logger="tarn_cli")


@dataclass(frozen=True, slots=True)
class Choice:
    college: SeededCollege
    blueprint: ExamBlueprint
    student: StudentId
    actor: UserId
    college_id: CollegeId


def seeded_world() -> tuple[InMemory, list[SeededCollege]]:
    mem = InMemory()
    return mem, seed_in_memory(mem, assets=asset_reader())


def _read_manifest(folder: Path) -> dict[str, str]:
    path = folder / MANIFEST
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise typer.BadParameter(f"{path.name} is not valid JSON") from None
    if not isinstance(data, dict):
        raise typer.BadParameter(f"{path.name} must hold a JSON object")
    return {k: v for k, v in data.items() if k in ("college", "exam", "usn") and isinstance(v, str)}


def choose(
    mem: InMemory,
    seeded: list[SeededCollege],
    *,
    college: str | None,
    exam: str | None,
    usn: str | None,
) -> Choice:
    """Resolve the college, exam and student the run is for; a vague or unknown name is an
    error that lists the choices, never a guess."""
    codes = [s.seed.institution_id for s in seeded]
    if college is None:
        raise typer.BadParameter(f"name the college (--college): {', '.join(codes)}")
    found = next((s for s in seeded if s.seed.institution_id.lower() == college.lower()), None)
    if found is None:
        raise typer.BadParameter(f"unknown college {college!r}: {', '.join(codes)}")
    college_id = found.accounts.college_id
    blueprints = sorted(mem.content.latest(ExamBlueprint), key=lambda b: b.title)
    if exam is None:
        raise typer.BadParameter("name the exam (--exam); `tarn exams` lists them")
    wanted = exam.casefold()
    hits = [
        b for b in blueprints if wanted in b.title.casefold() or wanted == b.course_code.casefold()
    ]
    if len(hits) != 1:
        names = "; ".join(b.title for b in blueprints)
        reason = "no exam matches" if not hits else "several exams match"
        raise typer.BadParameter(f"{reason} {exam!r}: {names}")
    students = mem.students.list(college_id)
    picked = students[0] if usn is None else None
    if usn is not None:
        picked = next((s for s in students if s.usn.casefold() == usn.casefold()), None)
    if picked is None:
        raise typer.BadParameter(f"no student with USN {usn!r} in {college}")
    return Choice(
        college=found,
        blueprint=hits[0],
        student=picked.id,
        actor=found.accounts.teacher_ids[0],
        college_id=college_id,
    )


def write_result(evaluated: Evaluated, out: Path) -> tuple[Path, Path | None]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / RESULT_JSON
    json_path.write_text(
        json.dumps(evaluated.result.as_json(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    pdf_path = None
    if evaluated.sheet_pdf is not None:
        pdf_path = out / RESULT_PDF
        pdf_path.write_bytes(evaluated.sheet_pdf)
    return json_path, pdf_path


def evaluate_command(
    folder: Annotated[
        str, typer.Argument(help="Folder with the booklet's pages or one PDF; '-' reads stdin.")
    ],
    college: Annotated[
        str | None, typer.Option(help="Demo college (institution id), e.g. DEMO_COM.")
    ] = None,
    exam: Annotated[
        str | None, typer.Option(help="Exam: part of its title or its course code.")
    ] = None,
    usn: Annotated[
        str | None, typer.Option(help="Student USN (default: first on the roster).")
    ] = None,
    out: Annotated[
        Path | None, typer.Option(help="Where to write the result (default: FOLDER/result).")
    ] = None,
    use_anyway: Annotated[
        bool, typer.Option("--use-anyway", help="Go on with pages the quality gate flags.")
    ] = False,
) -> None:
    """Evaluate one booklet end to end and write result.json and a draft result sheet PDF."""
    manifest: dict[str, str] = {}
    source: PageSource
    if folder == "-":
        source = StreamPageSource(sys.stdin.buffer)
        out = out or Path("tarn-result")
    else:
        path = Path(folder)
        source = FolderPageSource(path)
        manifest = _read_manifest(path) if path.is_dir() else {}
        out = out or path / "result"
    settings = get_settings()
    mem, seeded = seeded_world()
    choice = choose(
        mem,
        seeded,
        college=college or manifest.get("college"),
        exam=exam or manifest.get("exam"),
        usn=usn or manifest.get("usn"),
    )

    def progress(kind: str) -> None:
        typer.echo(f"  {kind}", err=True)

    evaluation = build_evaluation(
        mem,
        build_stages(settings, mem.blobs),
        source,
        limits=UploadLimits(
            max_total_bytes=settings.upload_max_bytes, max_files=settings.upload_max_pages
        ),
        on_step=progress,
    )
    typer.echo(
        f"evaluating for {choice.college.seed.institution_id}: {choice.blueprint.title}", err=True
    )
    try:
        evaluated = evaluation.evaluate(
            choice.college_id,
            choice.actor,
            student_id=choice.student,
            blueprint_id=choice.blueprint.id,
            use_anyway=use_anyway,
        )
    except DomainError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from None
    json_path, pdf_path = write_result(evaluated, out)
    result = evaluated.result
    typer.echo(f"status      : {result.status}")
    if result.complete:
        flagged = sum(1 for a in result.answers if a.flags)
        typer.echo(f"AI total    : {result.total} / {result.max_marks} (suggestion, not approved)")
        typer.echo(f"answers     : {len(result.answers)} scored, {flagged} flagged for the teacher")
    for r in result.retake:
        typer.echo(f"retake page : {r.page} ({', '.join(r.reasons)}); --use-anyway goes on")
    if result.failure_reason:
        typer.echo(f"failed      : {result.failure_reason}")
    for name in result.errors:
        typer.echo(f"stage error : {name}")
    typer.echo(f"result      : {json_path}")
    if pdf_path is not None:
        typer.echo(f"draft sheet : {pdf_path}")
    if result.status == BookletStatus.NEEDS_RETAKE.value:
        raise typer.Exit(code=EXIT_RETAKE)
    if not result.complete:
        raise typer.Exit(code=1)


def exams_command() -> None:
    """List the exams and student USNs of the demo colleges that `tarn evaluate` can use."""
    mem, seeded = seeded_world()
    names = {s.accounts.college_id: s.seed.institution_id for s in seeded}
    for s in seeded:
        students = mem.students.list(s.accounts.college_id)
        typer.echo(f"{s.seed.institution_id}  {s.seed.name}")
        typer.echo(f"  USNs: {', '.join(st.usn for st in students)}")
    typer.echo("exams:")
    for b in sorted(mem.content.latest(ExamBlueprint), key=lambda b: b.title):
        owner = names.get(b.meta.owning_college_id, "?")
        typer.echo(f"  {b.title} [{b.course_code or '-'}] {b.total_marks} marks (owner {owner})")
