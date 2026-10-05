"""Scoring adapters (P13): the scoring embedder runs on this machine only.

Design: "a small open sentence-embedding model run locally on CPU, so no student text leaves
Tarn in the non-LLM phase". The builder accepts ``trigram`` or a model id whose weights are
already cached, loaded with ``local_files_only``; anything else is refused at start-up."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tarn_adapters.config import Settings
from tarn_adapters.embed import wiring
from tarn_adapters.embed.sentence import SentenceTransformerEmbedder
from tarn_adapters.scoring.bench import bench, local_embedder
from tarn_adapters.scoring.sets import load_set, write_answers
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing import InMemory
from tarn_core.testing.seed_world import seed_in_memory


def settings(tmp_path: Path, model: str) -> Settings:
    return Settings(scoring_embedding_model=model, model_dir=tmp_path)


def cache(tmp_path: Path, model: str) -> None:
    snapshot = tmp_path / "huggingface" / f"models--{model.replace('/', '--')}" / "snapshots" / "x"
    snapshot.mkdir(parents=True)


@pytest.mark.parametrize(
    "model",
    [
        "https://api.example.com/v1/embeddings",
        "http://127.0.0.1:8080/embed",
        "openai:text-embedding-3-small",
        "groq/../../etc",
        "s3://bucket/model",
        "plainname",
        "",
    ],
)
def test_anything_but_a_local_model_id_is_refused(tmp_path: Path, model: str) -> None:
    with pytest.raises(wiring.RemoteEmbedderError):
        wiring.build_scoring_embedder(settings(tmp_path, model))


def test_trigram_is_accepted(tmp_path: Path) -> None:
    chosen = wiring.build_scoring_embedder(settings(tmp_path, "trigram"))
    assert isinstance(chosen.embedder, TrigramEmbedder) and chosen.fallback_reason is None


def test_a_cached_model_loads_from_local_files_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    model = "sentence-transformers/all-MiniLM-L6-v2"
    cache(tmp_path, model)
    chosen = wiring.build_scoring_embedder(settings(tmp_path, model))
    assert isinstance(chosen.embedder, SentenceTransformerEmbedder)
    assert chosen.embedder.local_files_only is True
    assert chosen.fallback_reason is None


def test_the_hub_is_never_asked_when_the_model_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag reaches the library: a stand-in SentenceTransformer records its arguments."""
    seen: dict[str, Any] = {}

    class FakeModel:
        def __init__(self, name: str, **kwargs: Any) -> None:
            seen.update(kwargs, name=name)

    import sys
    import types

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    model = "BAAI/bge-small-en-v1.5"
    cache(tmp_path, model)
    chosen = wiring.build_scoring_embedder(settings(tmp_path, model))
    assert isinstance(chosen.embedder, SentenceTransformerEmbedder)
    _ = chosen.embedder._model  # load
    assert seen["local_files_only"] is True
    assert seen["cache_folder"] == str(tmp_path / "huggingface")


def test_missing_weights_fall_back_to_trigram_instead_of_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    chosen = wiring.build_scoring_embedder(settings(tmp_path, "org/not-fetched"))
    assert isinstance(chosen.embedder, TrigramEmbedder)
    assert chosen.fallback_reason is not None
    assert "tarn score models fetch" in chosen.fallback_reason


def test_e5_models_get_their_query_prefix() -> None:
    assert SentenceTransformerEmbedder("intfloat/e5-small-v2")._prefix == "query: "
    assert SentenceTransformerEmbedder("sentence-transformers/all-MiniLM-L6-v2")._prefix == ""


# --- calibration sets and the benchmark (synthetic data) ----------------------------------------


QUESTIONS = {
    f"D-{n}": {
        "text": f"What does a stack do? ({n})",
        "max_marks": "5",
        "key": "A stack stores items last in first out. Push adds on top and pop removes the top.",
        "statements": [
            "A stack stores items last in first out.",
            "Push adds on top and pop removes the top.",
        ],
    }
    for n in range(1, 5)
}


def dataset(folder: Path) -> Path:
    rows = []
    for code in QUESTIONS:
        for k, (text, mark) in enumerate(
            [
                ("A stack is last in first out. Push adds on top, pop removes the top item.", "5"),
                ("Items are stored last in first out.", "2.5"),
                ("A queue is first in first out.", "0"),
            ]
        ):
            rows.append({"id": f"{code}:{k}", "question": code, "text": text, "teacher_mark": mark})
    write_answers(folder, rows)
    (folder / "questions.json").write_text(json.dumps(QUESTIONS), encoding="utf-8")
    return folder


def test_a_dataset_set_has_even_semantic_criteria(tmp_path: Path) -> None:
    data = load_set(dataset(tmp_path / "set"))
    assert len(data.questions) == 4 and len(data.answers) == 12 and data.marked_by == "dataset"
    q = data.questions[0]
    assert [c.weight for c in q.criteria] == [Decimal("2.5"), Decimal("2.5")]
    assert (tmp_path / "set" / "answers.jsonl").stat().st_mode & 0o077 == 0


def test_marks_json_wins_and_unmarked_answers_are_left_out(tmp_path: Path) -> None:
    folder = dataset(tmp_path / "set")
    marks = {"marked_by": "claude", "marks": {"D-1:0": "4"}}
    (folder / "marks.json").write_text(json.dumps(marks), encoding="utf-8")
    rows = [json.loads(x) for x in (folder / "answers.jsonl").read_text().splitlines()]
    rows[1]["teacher_mark"] = None
    write_answers(folder, rows)
    data = load_set(folder)
    assert data.marked_by == "claude" and data.unmarked == 1
    assert data.answers[0].teacher_mark == Decimal(4)


def test_seeded_questions_are_found_by_code(tmp_path: Path) -> None:
    folder = tmp_path / "samples"
    write_answers(
        folder,
        [{"id": "b:12", "question": "QP-CI-Q12", "text": "He appoints governors."}],
    )
    (folder / "marks.json").write_text(json.dumps({"marks": {"b:12": "1"}}), encoding="utf-8")
    memory = InMemory()
    seed_in_memory(memory)
    data = load_set(folder, memory)
    (q,) = data.questions
    assert q.code == "QP-CI-Q12" and "Congress" in q.off_target_terms
    assert q.max_marks == Decimal(5) and data.marked_by == "teacher"


def test_bench_runs_the_trigram_baseline_and_skips_unfetched_models(tmp_path: Path) -> None:
    data = load_set(dataset(tmp_path / "set"))
    said: list[str] = []
    results, baseline = bench(["trigram", "org/not-fetched"], data, tmp_path, said.append)
    assert baseline is not None and baseline[0].mae > 0
    assert [r.model for r in results] == ["trigram"]
    assert results[0].answers == 12 and results[0].spearman > 0.5
    assert any("org/not-fetched: skipped" in s for s in said)
    with pytest.raises(wiring.RemoteEmbedderError):
        local_embedder("https://example.com/embed", tmp_path)


def test_mohler_layout_imports_as_a_set(tmp_path: Path) -> None:
    """A synthetic copy of the dataset's layout: excluded questions, <br>, -LRB-, <STOP>."""
    from tarn_adapters.scoring.mohler import import_mohler

    data = tmp_path / "mohler" / "data"
    for folder in ("docs", "raw", "sent", "scores/1.1", "scores/1.2"):
        (data / folder).mkdir(parents=True)
    (data / "docs" / "files").write_text("1.1\n#1.2\n")
    (data / "raw" / "questions").write_text("1.1 What is a stack?\n1.2 Order these.\n")
    (data / "raw" / "answers").write_text("1.1 A LIFO list. Push and pop.\n1.2 b, a\n")
    (data / "sent" / "answers").write_text(
        "1.1 A LIFO list -LRB- last in -RRB- <STOP> Push and pop <STOP>\n"
    )
    (data / "raw" / "1.1").write_text("1.1 last in first out<br>\n1.1 a queue\n")
    (data / "scores" / "1.1" / "ave").write_text("4.5\n0\n")
    out = tmp_path / "set"
    assert import_mohler(tmp_path / "mohler", out) == (1, 2)
    data_set = load_set(out)
    (q,) = data_set.questions
    assert q.code == "M-1.1" and q.max_marks == Decimal(5)
    statements = [c.params.reference_statement for c in q.criteria]  # type: ignore[union-attr]
    assert statements == ["A LIFO list (last in)", "Push and pop"]
    assert [a.teacher_mark for a in data_set.answers] == [Decimal("4.5"), Decimal(0)]
    assert data_set.answers[0].text == "last in first out"
