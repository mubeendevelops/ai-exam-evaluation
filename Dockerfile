# syntax=docker/dockerfile:1
#
# Single image for both the API (api.main:app) and the worker
# (scripts/run_job_worker.py) — docker-compose.yml picks which one runs via
# `command:`. Both need the same core/ library and the same Python env, so
# one image is the whole point (§11 Rule 1: api/ and scripts/ are two front
# doors onto one core/ library — this just ships that pair together).
FROM python:3.10-slim AS base

# ── OS packages ──────────────────────────────────────────────────────────
# tesseract-ocr   — core/ocr_engines/tesseract_engine.py shells out to the
#                   `tesseract` binary; it is NOT pip-installable
#                   (requirements.txt's pytesseract note; CLAUDE_CONTEXT.md §7).
# postgresql-client — scripts/migrate.py applies migration files via `psql -f`,
#                   not psycopg2: migrations 016/017 use psql meta-commands
#                   (\getenv, \if) to read APP_DB_USER/APP_DB_PASSWORD without
#                   a literal password in a committed file, and psycopg2 sends
#                   raw SQL with no client-side preprocessing — it cannot run
#                   those two files at all. Also used by
#                   scripts/reset_and_seed_db.sh (pg_dump/psql).
# libgl1, libglib2.0-0, libgomp1 — runtime shared libs paddleocr/paddlepaddle
#                   and opencv-python-headless dlopen on a slim base image
#                   (no X11/GTK stack otherwise present here).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        postgresql-client \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies — its own layer, cached separately from app code ──
# This is the expensive layer (paddleocr/paddlepaddle/sentence-transformers
# — requirements.txt's own comments describe past version fights in this
# stack). Copying only requirements.txt first means an app-code-only change
# never invalidates it; the layer is only rebuilt when requirements.txt
# itself changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    # boto3 is deliberately commented out of requirements.txt (only needed
    # for --storage minio / STORAGE_MODE=minio) — but docker-compose.yml
    # brings up a real MinIO alongside this image specifically so the
    # container path can be exercised for real, so it's installed here
    # rather than uncommenting a line whose comment explains why it is off
    # by default for the bare CLI/venv setup.
    && pip install --no-cache-dir boto3>=1.34

# ── App code — its own layer, changes on every commit ──────────────────────
COPY . .

# ── Non-root user ───────────────────────────────────────────────────────
# The image has no reason to run as root: it never installs packages or
# writes outside /app and DUMMY_STORAGE_ROOT (a mounted volume, see
# docker-compose.yml) at runtime.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Dummy-storage default from .env.example, given a stable, writable home
# under this user rather than the system temp dir.
ENV DUMMY_STORAGE_ROOT=/home/appuser/dummy-storage
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# docker-compose.yml overrides this for the worker service; the API is the
# default so `docker run` on this image alone does something sensible.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
