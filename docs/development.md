# Development environment

## Prerequisites

- Docker with Compose v2, [uv](https://docs.astral.sh/uv/) 0.12.x, Node.js 22.22+ or 24 LTS (`frontend/.nvmrc` has 24.21.0).
- Python 3.12.14 is downloaded by uv on first `make setup`; the host's own Python is not used.

## First run

```bash
make setup        # uv sync, npm ci, copy .env.example to .env
make up           # build and start postgres+pgvector, minio, api, worker, frontend
make lint typecheck test
```

| Service | URL | Notes |
| --- | --- | --- |
| Frontend | http://localhost:5173 | Vite dev server; proxies `/api` to the API container |
| API | http://localhost:8000/docs | `GET /api/v1/health` reports version and compute device |
| MinIO console | http://localhost:9001 | Credentials in `.env` (`MINIO_ROOT_*`) |
| PostgreSQL | localhost:5432 | Credentials in `.env` (`POSTGRES_*`); `vector` extension preinstalled |

All ports are bound to 127.0.0.1. Data lives in the named volumes `postgres-data` and `minio-data`; `make down` keeps them, `docker compose down -v` deletes them.

`make test-integration` runs the tests that need the stack (PostgreSQL has pgvector, the MinIO bucket exists). `uv run tarn doctor --services` (from `backend/`) prints the same checks.

## Tooling choices

- **uv** manages Python (workspace at `backend/`, one package per member, `backend/uv.lock` pins every dependency). Poetry was not used.
- **import-linter** (`backend/pyproject.toml`) fails `make lint` if `tarn_core` imports a web, database, HTTP, cloud, ML or adapter module, and enforces api/worker/cli → adapters → core layering.
- **MinIO is built from source** (`deploy/dev/minio/Dockerfile`, tag `RELEASE.2025-10-15T17-29-55Z`): the official image is gone and the upstream repository is archived. The first `make up` compiles it (a few minutes); later runs use the cache.

## GPU (optional)

Nothing requires a GPU. `TARN_DEVICE=auto` (the default) uses CUDA when torch can see a device and the CPU otherwise; `cpu` forces the CPU; `cuda` fails loudly if CUDA is missing. `tarn doctor` and `GET /api/v1/health` show what was chosen and, when a GPU exists but is unusable, why. `torch` itself is installed from P10 on, so until then the device is always the CPU.

The development laptop's GTX 1650 has 4 GB: load one model at a time.

To give the worker container the GPU, install the NVIDIA Container Toolkit on the host (Ubuntu/Debian; the NVIDIA driver must already work: `nvidia-smi`):

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Check it, then start the stack with the GPU override:

```bash
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
make up-gpu
```

`docker-compose.gpu.yml` reserves one NVIDIA device for the worker only. `make down` stops the stack in either mode.
