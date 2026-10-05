"""The real scoring model (P13, ``make test-models``): the configured sentence model scores a
booklet with every network call refused, so no student text can leave the machine; and it
keeps the off-target answer apart from the on-target one. Synthetic answers only."""

import importlib.util
import socket
from collections.abc import Iterator
from dataclasses import replace

import pytest

from tarn_adapters.config import Settings
from tarn_adapters.embed.sentence import SentenceTransformerEmbedder
from tarn_adapters.embed.wiring import build_scoring_embedder, model_cached
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.scoring import AnswerFlag
from tarn_core.seed.data.papers import QP_CI_TITLE
from tarn_core.services.scoring import BookletScorer, ScoringService
from tarn_core.testing import InMemory
from tarn_core.testing.builders import make_services
from tarn_core.testing.scoring import written_answer
from tarn_core.testing.seed_world import seed_in_memory

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(
        importlib.util.find_spec("sentence_transformers") is None,
        reason="sentence-transformers is not installed",
    ),
]

ON_TARGET = [
    "12. Under Article 53 the executive power of the Union is vested in the President.",
    "The President appoints the Prime Minister and the council of ministers.",
    "He appoints the Governors of the states and the judges of the Supreme Court.",
    "He is the Supreme Commander of the defence forces and can declare an emergency.",
]
OFF_TARGET = [
    "12. The President is the head of state and of the government.",
    "He signs the bills passed by Congress into law or sends them back.",
    "He is the commander in chief of the army, the navy and the Marines.",
    "He appoints ambassadors and federal judges with the consent of the Senate.",
]


class NetworkUsedError(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkUsedError("the scoring model tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield


@pytest.fixture(scope="module")
def settings() -> Settings:
    s = Settings()
    if not model_cached(s.model_dir / "huggingface", s.scoring_embedding_model):
        pytest.skip("scoring model not fetched (tarn score models fetch)")
    return s


def test_the_scoring_model_scores_a_booklet_without_the_network(
    settings: Settings, no_network: None
) -> None:
    setup = build_scoring_embedder(settings)
    assert isinstance(setup.embedder, SentenceTransformerEmbedder)
    assert setup.embedder.local_files_only and setup.fallback_reason is None

    mem = InMemory()
    colleges = seed_in_memory(mem)
    blueprint = next(b for b in mem.content.latest(ExamBlueprint) if b.title == QP_CI_TITLE)
    college = next(c for c in colleges if c.accounts.college_id == blueprint.meta.owning_college_id)
    college_id = college.accounts.college_id
    booklet = (
        make_services(mem)
        .booklets.register(
            college_id,
            college.accounts.teacher_ids[0],
            student_id=mem.students.list(college_id)[0].id,
            blueprint_id=blueprint.id,
            file_sha256="e" * 64,
        )
        .booklet
    )
    booklet = replace(booklet, status=BookletStatus.SEGMENTED, version=booklet.version + 1)
    mem.booklets.save(college_id, booklet)
    good = written_answer(mem, booklet, "12", ON_TARGET)
    scoring = ScoringService.standard(
        booklets=mem.booklets,
        scores=mem.scores,
        content=mem.content,
        runtime=mem.runtime,
        embedder=setup.embedder,
    )
    stage = BookletScorer(booklets=mem.booklets, scoring=scoring, runtime=mem.runtime)
    assert stage.step(college_id, booklet.id)  # the model loads and embeds offline
    assert mem.booklets.get(college_id, booklet.id).status is BookletStatus.SCORED

    on = mem.scores.scores(college_id, good.id)[-1]
    off_answer = written_answer(mem, booklet, "12", OFF_TARGET)
    off = scoring.score_answer(college_id, None, off_answer.id)
    assert on.embedder == setup.embedder.ref
    assert AnswerFlag.OFF_TARGET not in on.flags
    assert AnswerFlag.OFF_TARGET in off.flags
    assert on.mark is not None and off.mark is not None and on.mark > off.mark
    assert len(mem.scores.vectors(college_id, good.id, setup.embedder.ref)) == len(ON_TARGET)
