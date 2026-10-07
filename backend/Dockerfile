# syntax=docker/dockerfile:1
# One Dockerfile for the API, the worker and the CLI. By default the image includes the `ocr`
# dependency group (torch with CUDA, Paddle, Tesseract): about 6 GB; the GPU is used only with
# docker-compose.gpu.yml. Production builds two images from it: the worker with the default, the
# API (and migration job) with `--build-arg WITH_OCR=false`, which is small and starts fast.
# Development: the source is bind-mounted over /app/backend by docker-compose.yml, so code
# changes need no rebuild.
FROM python:3.12.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Tesseract (print OCR, P10) with English and orientation data; libgomp for Paddle.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr=5.3.0-2 tesseract-ocr-eng tesseract-ocr-osd libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ARG WITH_OCR=true
WORKDIR /app/backend

# Dependencies first, so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/
COPY adapters/pyproject.toml adapters/
COPY api/pyproject.toml api/
COPY worker/pyproject.toml worker/
COPY cli/pyproject.toml cli/
# The opencv-contrib-python shim (D74) is a path dependency: it must be there for the first sync.
COPY shims shims
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace $([ "$WITH_OCR" = "true" ] || echo --no-group ocr)

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev $([ "$WITH_OCR" = "true" ] || echo --no-group ocr)

RUN useradd --uid 1000 --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "tarn_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
