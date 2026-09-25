# Shared runtime image for the `api` and `worker` compose services — same uv
# workspace, same synced .venv; the two services differ only in `command:`
# (compose.yml). Build from the REPO ROOT (`docker build -f python/Dockerfile .`)
# so the whole workspace (python/, pyproject.toml, uv.lock) is in context.
#
# Pin base images by tag; record the digest here after the first real pull
# (`docker inspect --format='{{index .RepoDigests 0}}' <image>`) so a tag
# that later moves can't silently change what ships:
#   ghcr.io/astral-sh/uv:python3.12-bookworm-slim  ->  sha256:<fill in after first pull>
#   python:3.12-slim-bookworm                       ->  sha256:<fill in after first pull>

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_HTTP_TIMEOUT=300

WORKDIR /app

# Dependency layer first (cache-friendly): pyproject.toml + uv.lock rarely
# change independently of source, but changing source shouldn't bust this.
COPY pyproject.toml uv.lock ./
COPY python/ ./python/

# --no-dev: ruff/pyright/pytest/hypothesis/playwright etc. have no business
# in a runtime image. --frozen: the lockfile is the contract, never re-resolve
# in CI/prod.
#
# --mount=type=cache: a FAILED `RUN` layer is discarded whole by BuildKit, so
# without this every retry re-downloads every wheel from scratch. The cache
# mount lives outside the layer and survives failures, so a retry resumes
# where it left off. UV_LINK_MODE=copy (above) is what makes it safe to pull
# out of a mount that won't exist at runtime.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --system pitwall && useradd --system --gid pitwall --create-home pitwall

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/python /app/python
COPY --from=build /app/pyproject.toml /app/uv.lock ./

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

ARG GIT_SHA=dev
ARG BUILD_TIME=unknown
ENV GIT_SHA=${GIT_SHA} \
    BUILD_TIME=${BUILD_TIME}

# /app/data, NOT /data: compose mounts the pitwall-data volume at /app/data
# (matching WORKDIR, since every package resolves data/... relative to its
# CWD). Docker seeds a fresh named volume from the image path it covers,
# ownership included -- so chowning the wrong path left the volume
# root-owned and every write from the pitwall user failed with EACCES.
RUN mkdir -p /app/data && chown pitwall:pitwall /app/data
USER pitwall

# No CMD/ENTRYPOINT — compose.yml sets `command:` per service (api: uvicorn
# via `f1-api`; worker: the recorder, or a one-shot job via `docker compose
# run --rm worker <cmd>`).
