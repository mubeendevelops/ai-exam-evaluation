"""core/plugins/ — pluggable evaluation modules, one module per block type.

This is core/ocr_engines/ generalized one level upward. There, a plugin
(core.ocr_engines.base.OCREngine) is "one way to read text off an image" and
core/ocr_fallback.py is the driver that runs them without knowing which
engine is which. Here, a plugin (core.plugins.base.EvaluationPlugin) is "one
way to extract AND score one kind of answer block" (text, diagram, table,
formula, ...) and core/plugins/registry.py is the driver.

Adding a new evaluation module means dropping ONE file in this package with
an @register-decorated class; nothing else in the codebase changes — the
registry discovers it by scanning this package, exactly the way
core/ocr_fallback.py's roster is the only place OCR engines are named.

Layout:
    base.py         # ExtractionResult / EvaluationResult / EvaluationPlugin
    registry.py     # @register, get_plugin(), plugins_for(), list_plugins()
    persistence.py  # write_evaluation_result() — the append-only ledger write
    text_extraction.py   # first plugin: wraps core/ocr_fallback.py
"""
