# syntax=docker/dockerfile:1
# One image for the API, the worker and the CLI. Development: the source is bind-mounted over
# /app/backend by docker-compose.yml, so code changes need no rebuild.
FROM python:3.12.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app/backend

# Dependencies first, so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/
COPY adapters/pyproject.toml adapters/
COPY api/pyproject.toml api/
COPY worker/pyproject.toml worker/
COPY cli/pyproject.toml cli/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --uid 1000 --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "tarn_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
