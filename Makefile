VENV := .venv-paddleocr

test:
	set -a && . ./.env && set +a && $(VENV)/bin/python -m pytest -q

test-fast:
	set -a && . ./.env && set +a && $(VENV)/bin/python -m pytest -q -m "not slow"

test-cov:
	set -a && . ./.env && set +a && $(VENV)/bin/python -m pytest -q --cov=core --cov-report=term-missing

.PHONY: test test-fast test-cov
