"""core/ocr_engines/ — pluggable OCR backends, one class per engine.

Each engine implements core.ocr_engines.base.OCREngine. core/ocr_fallback.py
is the only thing that imports engines by name and drives them; the rest of
the pipeline (core/diagram_extractor.py) talks to core/ocr_fallback.py, not
to individual engines, so adding/removing/reordering engines never touches
extraction code.
"""
