"""Tests for core/plugins/ — registry discovery/lookup and the persistence
guardrails. Fast: no DB, no OCR model, no LLM.

The registry keeps module-level state (_REGISTRY, _IMPORT_FAILURES, the
_discovered flag), so every test that mutates it does so through the
`clean_registry` fixture, which snapshots and restores that state. Without
it, a test registering a fake plugin would leak into the next one.
"""
from __future__ import annotations

import sys
import textwrap

import pytest

from core.plugins import registry
from core.plugins.base import EvaluationPlugin, EvaluationResult, ExtractionResult
from core.plugins.persistence import RLSVisibilityError, write_evaluation_result


@pytest.fixture
def clean_registry():
    """Snapshots and restores the registry's module-level state so tests can
    register/unregister fakes without leaking into each other."""
    saved_registry = dict(registry._REGISTRY)
    saved_failures = dict(registry._IMPORT_FAILURES)
    saved_discovered = registry._discovered
    try:
        yield registry
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved_registry)
        registry._IMPORT_FAILURES.clear()
        registry._IMPORT_FAILURES.update(saved_failures)
        registry._discovered = saved_discovered


class FakePlugin(EvaluationPlugin):
    """Minimal conforming plugin — no models, no I/O."""

    name = "fake_plugin"
    version = "1.0.0"
    supported = ("table",)

    def supports(self, block_type):
        return block_type in self.supported

    def extract(self, source, *, stub=False):
        return ExtractionResult(content=source, confidence=0.5, metrics={})

    def evaluate(self, extracted, reference, *, stub=False):
        return _result()


def _result(**overrides) -> EvaluationResult:
    """An EvaluationResult carrying every metrics key persistence requires."""
    metrics = {
        "plugin": "fake_plugin",
        "plugin_version": "1.0.0",
        "evaluator_model": "fake-model",
        "latency_ms": 1.0,
    }
    metrics.update(overrides.pop("metrics", {}))
    kwargs = {
        "score": 7.0, "max_score": 10.0, "confidence": 0.9,
        "explanation": "fake", "metrics": metrics,
    }
    kwargs.update(overrides)
    return EvaluationResult(**kwargs)


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def test_the_shipped_text_extraction_plugin_is_registered():
    """The success criterion from the plugin-architecture task: dropping
    core/plugins/text_extraction.py in is enough for it to show up."""
    assert "text_extraction" in registry.list_plugins()

    plugin = registry.get_plugin("text_extraction")
    assert plugin.name == "text_extraction"
    assert plugin.version
    assert plugin.supports("text")
    assert not plugin.supports("diagram")


def test_register_adds_an_instance_retrievable_by_name(clean_registry):
    clean_registry.register(FakePlugin)

    plugin = clean_registry.get_plugin("fake_plugin")
    assert isinstance(plugin, FakePlugin)
    assert "fake_plugin" in clean_registry.list_plugins()


def test_register_returns_the_class_unchanged(clean_registry):
    assert clean_registry.register(FakePlugin) is FakePlugin


def test_register_rejects_a_duplicate_name(clean_registry):
    clean_registry.register(FakePlugin)

    class Collider(FakePlugin):
        pass

    with pytest.raises(ValueError, match="Duplicate plugin name"):
        clean_registry.register(Collider)


@pytest.mark.parametrize("attr", ["name", "version"])
def test_register_rejects_a_plugin_missing_name_or_version(clean_registry, attr):
    incomplete = type("Incomplete", (FakePlugin,), {attr: ""})

    with pytest.raises(ValueError, match=attr):
        clean_registry.register(incomplete)


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def test_plugins_for_returns_only_plugins_claiming_that_block_type(clean_registry):
    clean_registry.register(FakePlugin)

    names = [p.name for p in clean_registry.plugins_for("table")]
    assert "fake_plugin" in names
    assert "text_extraction" not in names

    assert "text_extraction" in [p.name for p in clean_registry.plugins_for("text")]


def test_plugins_for_returns_empty_for_an_unclaimed_block_type(clean_registry):
    assert clean_registry.plugins_for("no_such_block_type") == []


def test_plugins_for_skips_a_plugin_whose_supports_raises(clean_registry):
    """Same posture as FallbackOCR.recognize(): one broken module must not
    take the lookup down with it."""
    class Exploding(FakePlugin):
        name = "exploding_plugin"

        def supports(self, block_type):
            raise RuntimeError("boom")

    clean_registry.register(Exploding)

    with pytest.warns(RuntimeWarning, match="exploding_plugin"):
        names = [p.name for p in clean_registry.plugins_for("text")]

    assert "exploding_plugin" not in names
    assert "text_extraction" in names  # the rest still resolve


def test_get_plugin_raises_keyerror_listing_what_is_registered():
    with pytest.raises(KeyError, match="No evaluation plugin named"):
        registry.get_plugin("definitely_not_a_plugin")


# ---------------------------------------------------------------------------
# graceful skip of a broken plugin module
# ---------------------------------------------------------------------------

def _write_plugin_module(tmp_path, monkeypatch, filename, body):
    """Drops a module into a throwaway directory and points the registry's
    discovery scan at it (in addition to the real package), so discover()
    picks it up exactly as it would a real file in core/plugins/."""
    import core.plugins as plugins_pkg

    plugin_dir = tmp_path / "extra_plugins"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / filename).write_text(textwrap.dedent(body))

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(plugins_pkg, "__path__",
                        list(plugins_pkg.__path__) + [str(plugin_dir)])
    return plugin_dir


def test_a_plugin_that_fails_to_import_is_skipped_with_a_warning(
        clean_registry, tmp_path, monkeypatch):
    """A missing dependency (no tesseract binary, no paddleocr wheel, ...)
    must leave that plugin out of the registry — never crash discovery."""
    _write_plugin_module(tmp_path, monkeypatch, "broken_plugin.py", """
        import definitely_missing_dependency  # noqa: F401
    """)

    clean_registry._IMPORT_FAILURES.clear()

    with pytest.warns(RuntimeWarning, match="broken_plugin"):
        clean_registry.discover(force=True)

    # The registry survived, and the working plugin is still there.
    assert "text_extraction" in clean_registry.list_plugins()
    assert "broken_plugin" in clean_registry.import_failures()


def test_get_plugin_reports_the_import_error_for_a_broken_plugin(
        clean_registry, tmp_path, monkeypatch):
    """'Not available because its import failed' must not be reported as
    'no such plugin' — the fix for the two is completely different."""
    _write_plugin_module(tmp_path, monkeypatch, "broken_plugin.py", """
        raise RuntimeError("tesseract binary not on PATH")
    """)

    clean_registry._IMPORT_FAILURES.clear()
    with pytest.warns(RuntimeWarning):
        clean_registry.discover(force=True)

    with pytest.raises(KeyError, match="tesseract binary not on PATH"):
        clean_registry.get_plugin("broken_plugin")


def test_a_new_plugin_file_needs_no_other_change_to_be_discovered(
        clean_registry, tmp_path, monkeypatch):
    """The architecture's headline promise: drop a file in core/plugins/
    with @register and touch nothing else."""
    _write_plugin_module(tmp_path, monkeypatch, "dropped_in_plugin.py", """
        from core.plugins.base import EvaluationPlugin, ExtractionResult
        from core.plugins.registry import register


        @register
        class DroppedInPlugin(EvaluationPlugin):
            name = "dropped_in"
            version = "0.1.0"

            def supports(self, block_type):
                return block_type == "formula"

            def extract(self, source, *, stub=False):
                return ExtractionResult(content=source, confidence=1.0, metrics={})

            def evaluate(self, extracted, reference, *, stub=False):
                raise NotImplementedError
    """)

    clean_registry.discover(force=True)

    try:
        assert "dropped_in" in clean_registry.list_plugins()
        assert [p.name for p in clean_registry.plugins_for("formula")] == ["dropped_in"]
    finally:
        sys.modules.pop("core.plugins.dropped_in_plugin", None)


# ---------------------------------------------------------------------------
# persistence guardrails (migration 012's XOR, and required metrics)
# ---------------------------------------------------------------------------

class ExplodingConnection:
    """A connection that fails loudly if touched. The reference-XOR checks
    must reject a bad call BEFORE any SQL runs, so a passing test here also
    proves no statement was issued."""

    def cursor(self):
        raise AssertionError(
            "write_evaluation_result touched the database before validating its "
            "reference arguments"
        )

    def rollback(self):
        raise AssertionError("write_evaluation_result rolled back before validating")

    def commit(self):
        raise AssertionError("write_evaluation_result committed before validating")


def test_persistence_refuses_both_reference_fks_set():
    with pytest.raises(ValueError, match="not both"):
        write_evaluation_result(
            ExplodingConnection(),
            answer_block_id="00000000-0000-0000-0000-000000000001",
            result=_result(),
            reference_answer_variant_id="00000000-0000-0000-0000-000000000002",
            reference_asset_id="00000000-0000-0000-0000-000000000003",
        )


def test_persistence_refuses_neither_reference_fk_set():
    with pytest.raises(ValueError, match="Got neither"):
        write_evaluation_result(
            ExplodingConnection(),
            answer_block_id="00000000-0000-0000-0000-000000000001",
            result=_result(),
        )


def test_persistence_refuses_metrics_missing_a_required_key():
    incomplete = EvaluationResult(
        score=1.0, max_score=10.0, confidence=0.5, explanation="",
        metrics={"plugin": "fake_plugin"},
    )

    with pytest.raises(ValueError, match="missing required key"):
        write_evaluation_result(
            ExplodingConnection(),
            answer_block_id="00000000-0000-0000-0000-000000000001",
            result=incomplete,
            reference_asset_id="00000000-0000-0000-0000-000000000003",
        )


class FakeCursor:
    """Cursor returning zero rows for the answer_block lookup and a scripted
    RLS context for the diagnostic query — i.e. exactly what a real cursor
    does when the tenant GUC wasn't set (RLS fails closed and silently)."""

    def __init__(self, rls_row):
        self._rls_row = rls_row
        self._last = None

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        return self._rls_row if "current_setting" in (self._last or "") else None

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rls_row=(None, None)):
        self._rls_row = rls_row
        self.rolled_back = False
        self.committed = False

    def cursor(self):
        return FakeCursor(self._rls_row)

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True


def test_persistence_raises_loudly_when_the_lookup_returns_zero_rows():
    """RLS fails closed silently (CLAUDE_CONTEXT.md §6) — zero rows must be
    an explicit error naming the missing GUC, never a quiet 'no data'."""
    conn = FakeConnection(rls_row=(None, None))

    with pytest.raises(RLSVisibilityError) as excinfo:
        write_evaluation_result(
            conn,
            answer_block_id="00000000-0000-0000-0000-000000000001",
            result=_result(),
            reference_asset_id="00000000-0000-0000-0000-000000000003",
        )

    message = str(excinfo.value)
    assert "NEITHER app.current_college_id NOR app.is_platform_admin is set" in message
    assert "app.is_platform_admin" in message
    assert conn.rolled_back and not conn.committed


def test_persistence_zero_rows_under_a_set_rls_context_still_raises():
    """With the GUC correctly set, zero rows means a bad block_id — still an
    error, and the message says which context was active."""
    conn = FakeConnection(rls_row=(None, "true"))

    with pytest.raises(RLSVisibilityError, match="platform admin"):
        write_evaluation_result(
            conn,
            answer_block_id="00000000-0000-0000-0000-000000000001",
            result=_result(),
            reference_answer_variant_id="00000000-0000-0000-0000-000000000002",
        )
