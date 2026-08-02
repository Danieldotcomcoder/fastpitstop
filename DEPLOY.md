# Deploy

How to run the whole stack in containers locally, and how to deploy it to a
single VPS. Written to be readable in six months having forgotten
everything. See [`CLAUDE.md`](./CLAUDE.md) for the product/phase context;
this document is purely operational.

No Kubernetes, no service mesh. One VPS, `docker compose`, Caddy for TLS.
This is a beta with an unknown number of users — optimise for "I can debug
it at 2am" over elasticity.

## Architecture

Five services (`compose.yml`), always run as base + one overlay:

- **db** — TimescaleDB + pgvector (`timescale/timescaledb-ha:pg16`).
- **api** — the FastAPI read model (`python/Dockerfile`).
- **web** — the SvelteKit frontend, `adapter-node` (`apps/web/Dockerfile`).
- **worker** — same image as `api`; runs the recorder long-lived by default,
  or one-shot jobs (backfill, radio ingest, an ad-hoc migration) via
  `docker compose run --rm worker <command>`.
- **proxy** — Caddy: automatic TLS, reverse-proxies `/api/*` (prefix
  stripped) to `api` and everything else to `web`, including the
  `/stream/{key}` WebSocket upgrade (automatic in Caddy, no special config).

```
Internet ──▶ proxy (Caddy, :443) ──┬─▶ /api/* ──▶ api:8000 ──▶ db:5432
                                    └─▶ /*     ──▶ web:3000
worker ──▶ db:5432 (ingest jobs), data volume (recordings/radio audio)
```

One named volume, `pitwall-data`, mounted at `/app/data` in both `api` and
`worker` — every Python package already resolves `data/recordings`,
`data/historical`, `data/fastf1_cache`, `data/radio_audio` as relative paths
against its CWD (`/app`), so this one mount covers all four. Recordings and
the radio corpus live there, never in an image layer — losing a live
recording is losing something unrepeatable.

## Local: run the whole stack in containers

```sh
just setup   # if you haven't already (uv sync + pnpm install)
just up      # build, start db+api+web with hot reload, migrate, seed one race
```

`just up` waits for the database to actually be healthy (`docker compose up
--wait`), applies migrations, then seeds one race from the same checked-in
synthetic recording the test suites use, so a fresh clone has something to
look at with zero manual steps. Then:

- API: <http://localhost:8000> (docs at `/docs`, health at `/healthz`,
  `/readyz`, `/version`)
- Frontend: <http://localhost:5173> (hot reload via `pnpm dev` inside the
  container)
- Database: `postgresql://pitwall:pitwall@localhost:5432/pitwall` (published
  in dev only — attach `psql` or a GUI directly)

`just down` stops it (data survives in named volumes; `docker compose -f
compose.yml -f compose.dev.yml down -v` if you actually want to wipe it).

Dev bind-mounts `python/` and `apps/web/` for hot reload, so day-to-day
backend/frontend work doesn't need `just up` at all — `just serve-api` /
`just web-dev` against the existing `python/f1_store/docker-compose.yml`
Postgres is still the fastest inner loop. `just up` is for proving the
*container* path works, and for demos.

## First-time host setup (the VPS)

1. A Linux VPS with a public IP. 2 vCPU / 4 GB RAM is comfortable for a
   beta; TimescaleDB + api + web + worker + Caddy all fit.
2. DNS: an `A` record for your domain pointing at the VPS's IP (and an
   `AAAA` if you have IPv6). Caddy needs this resolvable before it can get a
   cert — get DNS live first, deploy second.
3. Install Docker + the compose plugin:
   ```sh
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker "$(whoami)"   # log out/in to pick this up
   ```
4. Open firewall ports 80 and 443 (443 for HTTPS, 80 for the ACME HTTP-01
   challenge Caddy uses to issue the first cert). Leave 5432/8000/3000
   closed to the internet — nothing but `proxy` is published in prod
   (`compose.prod.yml`).
5. Create the deploy directory and put the repo's compose files on the host
   (only `compose.yml`, `compose.prod.yml`, `deploy/Caddyfile` and `.env`
   are needed there — the images themselves come from GHCR, not a git
   checkout; the easiest way to get these four files across is `scp`, or a
   shallow `git clone --depth 1` if you'd rather keep the host in sync with
   a checkout):
   ```sh
   sudo mkdir -p /opt/pitwall && sudo chown "$(whoami)" /opt/pitwall
   scp compose.yml compose.prod.yml .env.example $DEPLOY_USER@$DEPLOY_HOST:/opt/pitwall/
   ssh $DEPLOY_USER@$DEPLOY_HOST "mkdir -p /opt/pitwall/deploy"
   scp deploy/Caddyfile $DEPLOY_USER@$DEPLOY_HOST:/opt/pitwall/deploy/
   ssh $DEPLOY_USER@$DEPLOY_HOST "cd /opt/pitwall && cp .env.example .env"
   # then fill in every value in /opt/pitwall/.env on the host — see step 6.
   ```
6. Fill in `/opt/pitwall/.env` on the host:
   - `POSTGRES_PASSWORD` — a real secret, not `pitwall`.
   - `PUBLIC_F1_API_BASE_URL=https://<your-domain>/api` — must be absolute
     (scheme + host), not a bare path; see `apps/web/.env.example`.
   - `DOMAIN=<your-domain>`
   - `F1_FEEDBACK_IP_SALT`, `F1_TURNSTILE_SECRET`, the four `F1_SMTP_*` vars
     (all four or none — see `f1_api.env_check`), `ANTHROPIC_API_KEY` if you
     want live why-engine explanations.
   - `GHCR_NAMESPACE=<github-owner>/<repo>` (lowercase).
   - Leave `IMAGE_TAG` as-is — `just deploy` manages it.
7. Log into GHCR on the host so `docker compose pull` can fetch private
   images (skip if the GHCR packages are public):
   ```sh
   echo "$GHCR_PAT" | docker login ghcr.io -u <github-username> --password-stdin
   ```
8. First deploy — from your own machine (see below), then on the host:
   ```sh
   cd /opt/pitwall
   docker compose -f compose.yml -f compose.prod.yml run --rm worker f1-store-migrate
   ```
   (migrations run as an explicit step, never automatically on API boot —
   two API replicas racing on a migration is a bad first outage).

## Deploying

From your own machine, with `GHCR_NAMESPACE`, `DEPLOY_HOST`, `DEPLOY_USER`,
`DEPLOY_PATH` set (in your local `.env`, or exported):

```sh
just deploy
```

This builds both images tagged with the short git SHA, pushes them to GHCR,
then SSHes to the host, updates `.env`'s `IMAGE_TAG`, `docker compose pull`s,
and brings the stack up with `--wait`. Also writes `.last-deployed-sha` on
the host (the *previous* tag) before overwriting it, so:

```sh
just rollback
```

points `IMAGE_TAG` back at that previous tag and re-runs `up -d --wait`. No
rebuild, no redeploy from scratch — just switching which already-pushed
image tag is running.

CI (`.github/workflows/ci.yml`) also builds and pushes both images to GHCR
on every push to `main`, after `just check` passes — but deploy itself stays
manual (`just deploy`). A human is in the loop during the beta on purpose.

New migrations don't run automatically on deploy — after `just deploy`,
if this release added a migration:

```sh
ssh $DEPLOY_USER@$DEPLOY_HOST "cd $DEPLOY_PATH && docker compose -f compose.yml -f compose.prod.yml run --rm worker f1-store-migrate"
```

## Running a live recording session in production

The recorder (`worker`, `command: f1-record` by default, `restart:
unless-stopped`) is always running once the stack is up — it reconnects on
its own on any connection loss (see `f1_ingest.cli`) and writes straight to
the `pitwall-data` volume, so nothing extra is needed to "start" a session.

An hour before every race weekend, from the host:

```sh
cd /opt/pitwall
docker compose -f compose.yml -f compose.prod.yml run --rm worker \
  python python/f1_store/tools/record_check.py
```

Checks disk space, database reachability, and that the recordings path is
actually writable. A missed race weekend is a permanent gap in the dataset
— don't skip this.

After the session, ingest the recording into the database (this does not
happen automatically — `mode=live`'s direct SignalR→API wiring is a known
gap, see `CLAUDE.md`'s Phase 3 status):

```sh
docker compose -f compose.yml -f compose.prod.yml run --rm worker \
  f1-pipeline replay /app/data/recordings/<session-dir> --round <N>
```

## Backup and restore

```sh
just backup prod
```

Runs `pg_dump -Fc` against the `db` service via `docker compose exec`
(no host-installed Postgres client needed) into
`backups/pitwall-prod-<timestamp>.dump`. Copy this off the host regularly
(the VPS itself is not a backup strategy).

**Restore is tested, not just documented** — an untested backup is not a
backup:

```sh
just restore backups/pitwall-prod-<timestamp>.dump pitwall_restore_test prod
```

This always restores into a **scratch** database (`--target-db` must not be
the live `POSTGRES_DB` — the script refuses if it is), so you can inspect
row counts and diff against expectations before ever touching production
data. Drop the scratch database when done (the script prints the exact
command).

## Health, logs, status

- `GET /healthz` — liveness, no DB call.
- `GET /readyz` — checks database connectivity and returns
  `last_ingest_at` (the newest event timestamp in the store). 503 if the
  database is unreachable or unconfigured.
- `GET /version` — `git_sha`/`build_time`, read from the `GIT_SHA`/
  `BUILD_TIME` build args both Dockerfiles set.
- `/status` on the frontend reads all three and shows the web image's own
  build info alongside the API's — the whole stack's health from a phone.

Logs are structured JSON to stdout (`f1_api.logging_config`), with a
request ID propagated from `proxy` through `api` (`X-Request-Id`, generated
if the client didn't send one):

```sh
docker compose -f compose.yml -f compose.prod.yml logs -f api | jq .
docker compose -f compose.yml -f compose.prod.yml logs -f worker
```

`compose.prod.yml` caps log growth (`json-file`, 10 MB × 3 files per
service) — logs rotate, they don't fill the disk.

## Config validation

`f1_api` validates required production env vars at startup — via
`f1_api.env_check.validate_production_env`, only active when
`F1_ENV=production` (which `compose.prod.yml` sets for `api`). A missing
`F1_STORE_DSN`/`F1_FEEDBACK_IP_SALT`/`F1_TURNSTILE_SECRET`, a
still-default feedback salt, or a partially-configured SMTP block fails the
container at boot with a clear message, rather than serving traffic in a
broken state. `docker compose logs api` shows exactly which variable is
missing.

## Secrets

Never baked into an image, never in git history: `.env` is gitignored and
only ever read at container start via `env_file:`. CI's `build-and-push`
job greps the built frontend bundle for anything resembling a key
(`sk-...`, `ANTHROPIC_API_KEY`, SMTP/Turnstile/salt variable names, PEM
private-key headers) and fails the build if it finds one — the frontend
must ship with zero secrets, by construction (everything it uses is either
public config or fetched from `f1_api` at runtime).
