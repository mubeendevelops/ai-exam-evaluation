"""``tarn evaluate``'s world: the core's in-memory adapters (the reference implementations its
contract tests run against) holding the demo college and exam content for one run, the real
stages of ``tarn_adapters.stages`` on top, and the folder or stream page source.

Nothing is stored: the run leaves two files (the result JSON and the draft sheet PDF) and no
database, queue, object store or network is needed. The LLM scorer is never part of it: the
local colleges have the flag off and no scorer is passed, so no answer text leaves the machine
(D131)."""

from collections.abc import Callable

from tarn_adapters.sheets import PyMuPdfSheetRenderer
from tarn_adapters.stages import LocalStageRunner, Stages
from tarn_core.ports.rendering import SheetRenderer
from tarn_core.ports.storage import PageSource
from tarn_core.services.booklets import BookletService
from tarn_core.services.evaluation import BookletEvaluation
from tarn_core.services.pipeline import PageDecisions
from tarn_core.services.totals import TotalsService
from tarn_core.services.uploads import UploadLimits, UploadService
from tarn_core.services.workflow import SheetBuilder
from tarn_core.testing import InMemory


def build_evaluation(
    mem: InMemory,
    stages: Stages,
    source: PageSource,
    *,
    limits: UploadLimits | None = None,
    renderer: SheetRenderer | None = None,
    on_step: Callable[[str], None] | None = None,
) -> BookletEvaluation:
    """The evaluation use case over ``mem`` (blobs and queue included) and ``stages``."""
    rt = mem.runtime
    booklets = BookletService(
        booklets=mem.booklets,
        students=mem.students,
        users=mem.users,
        content=mem.content,
        scores=mem.scores,
        sheets=mem.sheets,
        blobs=mem.blobs,
        runtime=rt,
    )
    return BookletEvaluation(
        source=source,
        uploads=UploadService(
            booklets=booklets,
            repository=mem.booklets,
            blobs=mem.blobs,
            jobs=mem.jobs,
            runtime=rt,
            limits=limits,
        ),
        runner=LocalStageRunner(stages, mem, on_step=on_step),
        decisions=PageDecisions(booklets=mem.booklets, runtime=rt, jobs=mem.jobs),
        booklets=mem.booklets,
        scores=mem.scores,
        content=mem.content,
        totals=TotalsService(booklets=mem.booklets, scores=mem.scores, content=mem.content),
        sheets=SheetBuilder(
            booklets=mem.booklets,
            scores=mem.scores,
            content=mem.content,
            colleges=mem.colleges,
            students=mem.students,
            users=mem.users,
            blobs=mem.blobs,
        ),
        renderer=renderer or PyMuPdfSheetRenderer(),
        runtime=rt,
    )
