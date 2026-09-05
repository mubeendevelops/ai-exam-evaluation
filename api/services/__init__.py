"""api/services/ — thin orchestration between routers and core/.

A service coordinates core/ calls and transaction boundaries; it does not
contain evaluation logic. If a function here starts scoring, extracting, or
segmenting anything, it belongs in core/ instead (see api/__init__.py).
"""
