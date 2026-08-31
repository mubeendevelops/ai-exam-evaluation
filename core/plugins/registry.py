"""
core/plugins/registry.py — discovers and hands out EvaluationPlugin instances.

This is core/ocr_fallback.py's roster logic, generalized. There,
default_engines() names every OCREngine in one place and instantiates them
eagerly-but-cheaply (no model/binary is touched until recognize() actually
runs), so listing an engine whose dependency isn't installed is safe and it
only gets skipped when it fails at call time. The same two properties hold
here, with one difference: the roster isn't a hand-maintained list, it's
whatever modules live in core/plugins/ — so adding an evaluation module is
"drop in a file with @register" and nothing else in the codebase changes.

    @register
    class TableExtractionPlugin(EvaluationPlugin):
        name = "table_extraction"
        version = "0.1.0"
        ...

DEPENDENCY FAILURES ARE SKIPS, NOT CRASHES. A plugin module that can't be
imported at all (missing paddleocr, missing the tesseract binary, a typo)
is reported as a warning and left out of the registry — the rest of the
plugins keep working. That's deliberately the same posture as
core/ocr_engines/tesseract_engine.py (clear, actionable RuntimeError instead
of a cryptic one) combined with FallbackOCR.recognize()'s "an engine that
raises is skipped, not fatal": the whole point of having more than one
module is that the pipeline survives one of them being unavailable.
Registration itself is import-time and cheap; anything expensive belongs in
the plugin's own lazy loader, not its __init__.
"""
from __future__ import annotations

import importlib
import pkgutil
import warnings

from core.plugins.base import EvaluationPlugin

#: name -> instance. One shared instance per plugin (they cache their own
#: lazily-loaded models — see EvaluationPlugin's docstring).
_REGISTRY: dict[str, EvaluationPlugin] = {}

#: Modules in this package that are framework, not plugins, and so are never
#: scanned for registrations.
_NON_PLUGIN_MODULES = frozenset({"base", "registry", "persistence"})

#: Plugin modules that failed to import, name -> the exception. Kept (rather
#: than only warned about once) so a caller that expected a plugin to be
#: there can report WHY it isn't, instead of "no such plugin".
_IMPORT_FAILURES: dict[str, Exception] = {}

_discovered = False


def register(plugin_cls: type[EvaluationPlugin]) -> type[EvaluationPlugin]:
    """Class decorator: instantiates the plugin and adds it to the registry.

    Returns the class unchanged so the decorated name still refers to the
    class (importable and subclassable for tests). Raises on a duplicate
    name or a missing name/version rather than silently shadowing an
    existing plugin — a name collision is a bug in the new module, not a
    runtime condition to tolerate.
    """
    name = getattr(plugin_cls, "name", None)
    version = getattr(plugin_cls, "version", None)
    if not name:
        raise ValueError(f"{plugin_cls.__name__} must define a non-empty class attribute 'name'")
    if not version:
        raise ValueError(f"{plugin_cls.__name__} (name={name!r}) must define 'version' (semver)")
    if name in _REGISTRY:
        existing = type(_REGISTRY[name])
        raise ValueError(
            f"Duplicate plugin name {name!r}: {plugin_cls.__module__}.{plugin_cls.__name__} "
            f"collides with the already-registered {existing.__module__}.{existing.__name__}. "
            f"Plugin names are the registry key and must be unique."
        )

    _REGISTRY[name] = plugin_cls()
    return plugin_cls


def discover(force: bool = False) -> None:
    """Imports every non-framework module in core/plugins/ so their @register
    decorators run. Idempotent; called automatically by the lookup functions
    below, so callers normally never invoke it directly.

    force=True re-scans the package for modules that appeared since the last
    scan. It does NOT re-import (or re-register) modules already in
    sys.modules — a plugin registers exactly once per process, so re-running
    its @register would be a duplicate-name error, not a refresh.

    A module that raises on import is warned about and skipped — see the
    module docstring.
    """
    global _discovered
    if _discovered and not force:
        return

    import core.plugins as plugins_pkg

    for module_info in pkgutil.iter_modules(plugins_pkg.__path__):
        module_name = module_info.name
        if module_name in _NON_PLUGIN_MODULES or module_name.startswith("_"):
            continue
        try:
            importlib.import_module(f"core.plugins.{module_name}")
        except Exception as exc:  # noqa: BLE001 — a broken plugin must not break the registry
            _IMPORT_FAILURES[module_name] = exc
            warnings.warn(
                f"Skipping evaluation plugin 'core.plugins.{module_name}': it failed to "
                f"import ({type(exc).__name__}: {exc}). The registry and every other "
                f"plugin still work; install this plugin's missing dependency to enable it.",
                RuntimeWarning,
                stacklevel=2,
            )

    _discovered = True


def get_plugin(name: str) -> EvaluationPlugin:
    """Returns the registered plugin instance called `name`.

    Raises KeyError with an actionable message — including the import error,
    if this plugin is missing precisely because its module failed to load.
    """
    discover()
    if name in _REGISTRY:
        return _REGISTRY[name]

    if name in _IMPORT_FAILURES:
        exc = _IMPORT_FAILURES[name]
        raise KeyError(
            f"Evaluation plugin {name!r} is not available: core/plugins/{name}.py failed "
            f"to import ({type(exc).__name__}: {exc}). Install its missing dependency."
        )
    raise KeyError(
        f"No evaluation plugin named {name!r}. Registered: {sorted(_REGISTRY)}"
        + (f"; failed to import: {sorted(_IMPORT_FAILURES)}" if _IMPORT_FAILURES else "")
    )


def plugins_for(block_type: str) -> list[EvaluationPlugin]:
    """Every registered plugin whose supports(block_type) is true, in stable
    name order. Returns a list, not one plugin: more than one module may
    legitimately handle the same block_type (e.g. two diagram scorers being
    compared), and choosing between them is the caller's decision, not the
    registry's. A plugin whose supports() itself raises is skipped with a
    warning — same posture as an import failure."""
    discover()

    matched = []
    for name, plugin in sorted(_REGISTRY.items()):
        try:
            if plugin.supports(block_type):
                matched.append(plugin)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"Plugin {name!r}.supports({block_type!r}) raised "
                f"({type(exc).__name__}: {exc}) — treating it as 'does not support' "
                f"rather than failing the lookup.",
                RuntimeWarning,
                stacklevel=2,
            )
    return matched


def list_plugins() -> list[str]:
    """Sorted names of every successfully registered plugin. The quick
    health check for "did my new plugin file get picked up":

        python3 -c "from core.plugins.registry import list_plugins; print(list_plugins())"
    """
    discover()
    return sorted(_REGISTRY)


def import_failures() -> dict[str, str]:
    """Plugin module name -> the string form of the exception that kept it
    out of the registry. For diagnostics/CLI output; empty when everything
    imported cleanly."""
    discover()
    return {name: f"{type(exc).__name__}: {exc}" for name, exc in _IMPORT_FAILURES.items()}
