VENV := .venv-paddleocr

# Falls back to whatever `python3` is on PATH when .venv-paddleocr doesn't
# exist (CI: actions/setup-python + `pip install` into the runner's own
# environment, no repo-local venv at all) — local dev is unaffected, since
# the venv is there and wins.
ifneq ($(wildcard $(VENV)/bin/python),)
PYTHON := $(VENV)/bin/python
else
PYTHON := python3
endif

# .env is optional for the same reason: CI supplies PG*/API_ENV/etc. as real
# environment variables (no .env file in the checkout at all), while a local
# checkout still gets its usual values sourced from one when present.
DOTENV := $(if $(wildcard .env),set -a && . ./.env && set +a &&,)

test:
	$(DOTENV) $(PYTHON) -m pytest -q

test-fast:
	$(DOTENV) $(PYTHON) -m pytest -q -m "not slow"

test-cov:
	$(DOTENV) $(PYTHON) -m pytest -q --cov=core --cov-report=term-missing

.PHONY: test test-fast test-cov
