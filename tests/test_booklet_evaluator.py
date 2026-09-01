"""Tests for core/booklet_evaluator.py and core/llm.py's rate limiting.

Fast by construction: no DB, no OCR model, no LLM, no network. Two devices
make that possible —

  * the orchestration and aggregation half of core/booklet_evaluator.py is
    pure (its module docstring's "layering" section), so it can be driven
    entirely from hand-built BlockTasks; and
  * the plugins it routes to come from core/plugins/registry.py, which the
    `fake_plugins` fixture below swaps out wholesale — the same
    snapshot-and-restore approach tests/test_plugin_registry.py already uses.

Fakes rather than the real plugins on purpose: these tests are about
ORCHESTRATION (what happens when one region of twenty explodes, how several
regions of one question combine, how confidence survives that), and a fake
plugin is the only way to make a region fail on demand.
"""
from __future__ import annotations

import email.message
import io
import time
import urllib.error

import pytest

from core import booklet_evaluator as be
from core import llm
from core.plugins import registry
from core.plugins.base import EvaluationPlugin, EvaluationResult, ExtractionResult


# --- fake plugins --------------------------------------------------------

class Reference:
    """Stand-in for TextReference/TableReference — evaluate() only ever reads
    marks_max off it in these fakes."""

    def __init__(self, marks_max=10.0):
        self.marks_max = marks_max


class FakeTextPlugin(EvaluationPlugin):
    """Scores 0.1 marks per character of extracted text, so a merged answer
    scores measurably higher than either half — which is exactly the
    behaviour the merge exists to produce."""

    name = "fake_text"
    version = "1.0.0"

    def __init__(self):
        self.explode_on = set()

    def supports(self, block_type):
        return block_type == "text"

    def extract(self, source, *, stub=False):
        if source in self.explode_on:
            raise RuntimeError(f"OCR blew up on {source!r}")
        text = "" if stub else str(source)
        return ExtractionResult(content=text, confidence=_confidence_of(source),
                                metrics={"plugin": self.name, "mode": "fake"})

    def evaluate(self, extracted, reference, *, stub=False, stub_llm=False,
                 method=None, weights=None):
        score = min(reference.marks_max, round(len(extracted.content) * 0.1, 2))
        return EvaluationResult(
            score=score, max_score=reference.marks_max,
            confidence=extracted.confidence,
            explanation=f"fake text score {score}",
            metrics={"plugin": self.name, "plugin_version": self.version,
                     "evaluator_model": f"fake({method})", "latency_ms": 0.0,
                     "signals": {"semantic": {"score": score, "model": "fake"}},
                     "weights": {"semantic": 1.0}},
        )


class FakeTablePlugin(EvaluationPlugin):
    """Deliberately declares only `stub` on evaluate(), so the
    signature-inspecting kwargs builder is exercised against a plugin that
    does NOT take method/stub_llm."""

    name = "fake_table"
    version = "1.0.0"

    def supports(self, block_type):
        return block_type == "table"

    def extract(self, source, *, stub=False):
        return ExtractionResult(content={"cells": []}, confidence=0.9,
                                metrics={"plugin": self.name})

    def evaluate(self, extracted, reference, *, stub=False):
        score = float(str(extracted.content and 1) and len(str(extracted.content)) % 7)
        return EvaluationResult(
            score=score, max_score=reference.marks_max, confidence=0.9,
            explanation="fake table score",
            metrics={"plugin": self.name, "plugin_version": self.version,
                     "evaluator_model": "fake_table", "latency_ms": 0.0,
                     "comparison": {"structural_score": 0.5, "content_score": 0.5,
                                    "missing_rows": [1]}},
        )


def _confidence_of(source) -> float:
    """A source string may end in '@<confidence>' so a test can dictate one
    region's extraction confidence without another plumbing argument."""
    text = str(source)
    if "@" in text:
        return float(text.rsplit("@", 1)[1])
    return 1.0


@pytest.fixture
def fake_plugins():
    """Replace the whole registry with the two fakes above, then restore."""
    saved_registry = dict(registry._REGISTRY)
    saved_discovered = registry._discovered
    text_plugin, table_plugin = FakeTextPlugin(), FakeTablePlugin()
    registry._REGISTRY.clear()
    registry._REGISTRY.update({text_plugin.name: text_plugin,
                               table_plugin.name: table_plugin})
    registry._discovered = True
    try:
        yield text_plugin, table_plugin
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved_registry)
        registry._discovered = saved_discovered


def task(question, block_type="text", *, source="answer text", page=1, seq=1,
         marks_max=10.0, classification=1.0, reference=True, block_id=None,
         needs_review=False):
    return be.BlockTask(
        question_label=question, block_type=block_type, source=source,
        reference=Reference(marks_max) if reference else None,
        reference_kind="variant" if reference else None,
        reference_error=None if reference else "no current reference variant",
        marks_max=marks_max, page_number=page, sequence_order=seq,
        block_id=block_id, answer_id=f"answer-{question}",
        classification_confidence=classification, needs_review=needs_review,
        review_reasons=["low_confidence"] if needs_review else [],
    )


def run(tasks, **kwargs):
    kwargs.setdefault("max_concurrency", 1)
    return be.evaluate_booklet(tasks, **kwargs)


# --- merging and routing -------------------------------------------------

def test_text_regions_of_one_question_are_merged_into_one_answer(fake_plugins):
    report = run([task("Q1", source="first half", page=1, seq=1),
                  task("Q1", source="and the second half", page=2, seq=1)])

    question = report["questions"][0]
    assert len(question["components"]) == 1, "two text regions must score as one answer"
    component = question["components"][0]
    assert component["merged_from"] == 2
    # 'first half' + separator + 'and the second half' = 31 chars * 0.1
    assert component["score"] == pytest.approx(3.1)
    assert question["pages"] == [1, 2]


def test_merged_confidence_is_the_weakest_part_not_the_mean(fake_plugins):
    report = run([task("Q1", source="clean read@0.95", page=1, seq=1),
                  task("Q1", source="smudged read@0.20", page=2, seq=1)])

    component = report["questions"][0]["components"][0]
    assert component["extraction_confidence"] == pytest.approx(0.20)


def test_tables_are_not_merged_and_the_ambiguity_is_flagged(fake_plugins):
    report = run([task("Q1", "table", source="t1", page=1, seq=1),
                  task("Q1", "table", source="t2", page=1, seq=2)])

    question = report["questions"][0]
    assert len(question["components"]) == 2
    assert any(flag.startswith("multiple_table_regions") for flag in question["flags"])
    assert sum(1 for c in question["components"] if c["primary"]) == 1


def test_block_type_no_plugin_supports_is_a_failure_not_a_crash(fake_plugins):
    report = run([task("Q1", "formula", source="E=mc2")])

    assert report["totals"]["questions_scored"] == 0
    failure = report["failures"][0]
    assert failure["stage"] == "route"
    assert failure["error_type"] == "NoPluginError"
    assert "unscored" in report["questions"][0]["flags"]


# --- partial failure -----------------------------------------------------

def test_one_exploding_region_does_not_abort_the_booklet(fake_plugins):
    text_plugin, _ = fake_plugins
    text_plugin.explode_on = {"bad region"}

    report = run([task("Q1", source="good answer", page=1),
                  task("Q2", source="bad region", page=2),
                  task("Q3", source="another good answer", page=3)])

    assert report["totals"]["questions_scored"] == 2
    assert report["totals"]["questions_unscored"] == 1
    assert [f["stage"] for f in report["failures"]] == ["extract"]
    q2 = next(row for row in report["questions"] if row["question"] == "Q2")
    assert q2["score"] is None, "an unscorable answer is not a zero"
    assert q2["failures"][0]["error_type"] == "RuntimeError"


def test_missing_reference_is_reported_before_any_extraction(fake_plugins):
    text_plugin, _ = fake_plugins
    text_plugin.explode_on = {"answer text"}      # would fire if extract ran

    report = run([task("Q1", reference=False)])

    failure = report["failures"][0]
    assert failure["stage"] == "reference"
    assert "no current reference variant" in failure["message"]


def test_unscored_questions_count_against_the_total_but_not_the_attempt(fake_plugins):
    text_plugin, _ = fake_plugins
    text_plugin.explode_on = {"bad"}

    report = run([task("Q1", source="0123456789", marks_max=10.0),
                  task("Q2", source="bad", marks_max=10.0, page=2)])

    totals = report["totals"]
    assert totals["score"] == 1.0
    assert totals["max_score"] == 20.0             # the lost question still costs marks
    assert totals["attempted_max_score"] == 10.0   # ... but is excluded from "attempted"
    assert totals["percentage"] == pytest.approx(0.05)
    assert totals["attempted_percentage"] == pytest.approx(0.1)


# --- confidence propagation ---------------------------------------------

def test_question_confidence_takes_the_worst_classification_of_its_regions(fake_plugins):
    report = run([task("Q1", source="part one", page=1, seq=1, classification=0.99),
                  task("Q1", source="part two", page=2, seq=1, classification=0.31)])

    assert report["questions"][0]["confidence"] == pytest.approx(0.31)
    assert "low_confidence" in report["questions"][0]["flags"]


def test_booklet_confidence_is_dominated_by_the_weakest_question(fake_plugins):
    report = run([task("Q1", source="aaaaaaaaaa@0.99", page=1),
                  task("Q2", source="bbbbbbbbbb@0.99", page=2),
                  task("Q3", source="cccccccccc@0.10", page=3)])

    confidence = report["confidence"]
    assert confidence["weakest"] == pytest.approx(0.10)
    assert confidence["weakest_question"] == "Q3"
    # The whole point: the harmonic mean sits far below the arithmetic one
    # that would have hidden the bad region.
    assert confidence["harmonic_weighted"] < confidence["arithmetic_mean"] - 0.3
    assert confidence["booklet"] == pytest.approx(confidence["harmonic_weighted"])


def test_failed_regions_reduce_booklet_confidence_through_coverage(fake_plugins):
    text_plugin, _ = fake_plugins
    text_plugin.explode_on = {"bad"}

    clean = run([task("Q1", source="aaaa", page=1), task("Q2", source="bbbb", page=2)])
    lossy = run([task("Q1", source="aaaa", page=1), task("Q2", source="bbbb", page=2),
                 task("Q3", source="bad", page=3)])

    assert lossy["confidence"]["coverage"] == pytest.approx(2 / 3, abs=1e-4)
    assert lossy["confidence"]["booklet"] < clean["confidence"]["booklet"]


# --- rollups -------------------------------------------------------------

def test_pages_separate_questions_that_span_a_break(fake_plugins):
    report = run([task("Q1", source="only on page one", page=1, seq=1),
                  task("Q2", source="starts here", page=1, seq=2),
                  task("Q2", source="ends here", page=2, seq=1)])

    page1, page2 = report["pages"]
    assert page1["questions_complete_on_page"] == ["Q1"]
    assert page1["spanning_questions"] == ["Q2"]
    assert page2["spanning_questions"] == ["Q2"]
    assert page1["regions"] == 2 and page2["regions"] == 1


def test_review_queue_surfaces_unassigned_regions_that_are_persisted_nowhere(fake_plugins):
    report = run([task("Q1", source="fine")],
                 unassigned_regions=[{"page_number": 3, "bbox": [0, 0, 10, 10],
                                      "block_type": "text", "confidence": 0.4,
                                      "unassigned_reason": "no_preceding_marker"}])

    kinds = [item["kind"] for item in report["review_queue"]]
    assert "unassigned_region" in kinds
    assert report["totals"]["regions_unassigned"] == 1
    assert report["totals"]["regions_routable"] == 1


def test_text_plugin_signals_reach_the_report(fake_plugins):
    report = run([task("Q1", source="an answer")], method="blended")

    signals = report["questions"][0]["components"][0]["signals"]
    assert signals["semantic"]["weight"] == 1.0
    assert report["questions"][0]["components"][0]["evaluator_model"] == "fake(blended)"


def test_plugins_that_do_not_take_method_are_still_callable(fake_plugins):
    """The kwargs builder inspects each plugin's signature — a plugin with a
    single scoring path must not be handed a `method` it never declared."""
    report = run([task("Q1", "table", source="t")], method="blended")

    assert report["questions"][0]["scored"] is True
    assert report["questions"][0]["components"][0]["detail"]["structural_score"] == 0.5
    assert report["questions"][0]["components"][0]["detail"]["missing_rows_count"] == 1


def test_concurrency_produces_the_same_report_as_serial(fake_plugins):
    tasks = [task(f"Q{i}", source=f"answer number {i}", page=i) for i in range(1, 7)]

    serial = run(tasks, max_concurrency=1)
    parallel = run(tasks, max_concurrency=4)

    assert ([(row["question"], row["score"]) for row in serial["questions"]]
            == [(row["question"], row["score"]) for row in parallel["questions"]])


# --- core/llm.py rate limiting ------------------------------------------

def test_token_bucket_paces_requests_beyond_its_burst():
    bucket = llm.TokenBucket(rate_per_minute=600, capacity=2)   # 10/second

    started = time.monotonic()
    for _ in range(5):
        bucket.acquire()
    elapsed = time.monotonic() - started

    # 2 free (the burst), then 3 more at 10/second.
    assert elapsed >= 0.25
    assert bucket.stats()["acquired"] == 5


def test_token_bucket_refuses_rather_than_silently_waiting_forever():
    bucket = llm.TokenBucket(rate_per_minute=60, capacity=1)
    bucket.acquire()
    with pytest.raises(llm.RateLimitError):
        bucket.acquire(timeout=0.05)


def test_daily_quota_stops_a_runaway_loop():
    quota = llm.DailyQuota(limit=2)
    quota.consume()
    quota.consume()
    with pytest.raises(llm.RateLimitError):
        quota.consume()


def test_backoff_grows_and_stays_jittered(monkeypatch):
    monkeypatch.setenv("GROQ_BASE_BACKOFF", "1.0")
    monkeypatch.setenv("GROQ_MAX_BACKOFF", "60")

    first = [llm._backoff_delay(0, None) for _ in range(20)]
    fourth = [llm._backoff_delay(3, None) for _ in range(20)]

    assert all(0.5 <= d <= 1.0 for d in first)
    assert all(4.0 <= d <= 8.0 for d in fourth)
    assert len(set(first)) > 1, "no jitter means N workers retry in lockstep"
    # A server-supplied Retry-After overrides the curve.
    assert 3.0 <= llm._backoff_delay(0, 3.0) <= 3.5


def _http_error(code: int, retry_after: str | None = None):
    headers = email.message.Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.groq.com/x", code, "boom", headers,
                                  io.BytesIO(b'{"error": "rate limited"}'))


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_json_retries_a_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("GROQ_MAX_RPM", "0")          # throttle off; test the retry
    monkeypatch.setenv("GROQ_BASE_BACKOFF", "0.001")
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(429, retry_after="0")
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    assert llm._post_json("https://api.groq.com/x", {}) == {"ok": True}
    assert calls["n"] == 3


def test_post_json_gives_up_as_a_rate_limit_error(monkeypatch):
    monkeypatch.setenv("GROQ_MAX_RPM", "0")
    monkeypatch.setenv("GROQ_BASE_BACKOFF", "0.001")
    monkeypatch.setenv("GROQ_MAX_RETRIES", "2")

    def always_429(request, timeout=None):
        raise _http_error(429)

    monkeypatch.setattr(llm.urllib.request, "urlopen", always_429)
    with pytest.raises(llm.RateLimitError):
        llm._post_json("https://api.groq.com/x", {})


def test_post_json_does_not_retry_a_bad_api_key(monkeypatch):
    monkeypatch.setenv("GROQ_MAX_RPM", "0")
    calls = {"n": 0}

    def unauthorized(request, timeout=None):
        calls["n"] += 1
        raise _http_error(401)

    monkeypatch.setattr(llm.urllib.request, "urlopen", unauthorized)
    with pytest.raises(RuntimeError) as excinfo:
        llm._post_json("https://api.groq.com/x", {})
    assert not isinstance(excinfo.value, llm.RateLimitError)
    assert calls["n"] == 1, "retrying a 401 only delays finding out about it"
