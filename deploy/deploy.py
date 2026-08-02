"""`just deploy` — build both images, push to GHCR, then pull+roll on the
VPS over SSH. A human runs this deliberately during a beta (CLAUDE.md's own
"deploy stays manual for now — you want a human in the loop"); CI's
build-and-push job (.github/workflows/ci.yml) also pushes on every merge to
main, but this is the explicit "ship this checkout now" lever, independent
of what's on main.

Required env (see .env.example): GHCR_NAMESPACE, DEPLOY_HOST, DEPLOY_USER,
DEPLOY_PATH (the directory on the host containing compose.yml/.env — see
DEPLOY.md's first-time setup). Fails loudly, before doing anything, if any
are missing — a half-configured deploy is worse than a refused one.

    uv run python deploy/deploy.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = ["GHCR_NAMESPACE", "DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_PATH"]


def _require_env() -> dict[str, str]:
    values = {name: os.environ.get(name) for name in REQUIRED_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        joined = ", ".join(missing)
        print(f"missing required env var(s): {joined} — see .env.example", file=sys.stderr)
        raise SystemExit(2)
    return {k: v for k, v in values.items() if v is not None}


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            "no git commit to tag the image with (git rev-parse HEAD failed) — "
            "make at least one commit before deploying:\n"
            f"  {result.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return result.stdout.strip()


def _build_and_push(namespace: str, name: str, dockerfile: str, sha: str, build_time: str) -> None:
    tags = [f"ghcr.io/{namespace}/{name}:{sha}", f"ghcr.io/{namespace}/{name}:latest"]
    build_cmd = [
        "docker",
        "build",
        "-f",
        dockerfile,
        "--build-arg",
        f"GIT_SHA={sha}",
        "--build-arg",
        f"BUILD_TIME={build_time}",
    ]
    for tag in tags:
        build_cmd += ["-t", tag]
    build_cmd.append(str(REPO_ROOT))
    print(f"+ {' '.join(build_cmd)}")
    subprocess.run(build_cmd, check=True)
    for tag in tags:
        push_cmd = ["docker", "push", tag]
        print(f"+ {' '.join(push_cmd)}")
        subprocess.run(push_cmd, check=True)


def _remote_deploy_command(deploy_path: str, sha: str) -> str:
    return (
        "set -euo pipefail\n"
        f"cd {deploy_path}\n"
        'OLD_TAG=$(grep "^IMAGE_TAG=" .env 2>/dev/null | cut -d= -f2 || true)\n'
        '[ -n "$OLD_TAG" ] && echo "$OLD_TAG" > .last-deployed-sha || true\n'
        "if grep -q '^IMAGE_TAG=' .env 2>/dev/null; then\n"
        f'  sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG={sha}/" .env\n'
        "else\n"
        f'  echo "IMAGE_TAG={sha}" >> .env\n'
        "fi\n"
        "docker compose pull\n"
        "docker compose -f compose.yml -f compose.prod.yml up -d --wait\n"
        f'echo "deployed {sha}"\n'
    )


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    env = _require_env()
    sha = _git_sha()
    build_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"deploying {sha} (built {build_time})")
    _build_and_push(env["GHCR_NAMESPACE"], "pitwall-api", "python/Dockerfile", sha, build_time)
    _build_and_push(env["GHCR_NAMESPACE"], "pitwall-web", "apps/web/Dockerfile", sha, build_time)

    remote_cmd = _remote_deploy_command(env["DEPLOY_PATH"], sha)
    ssh_cmd = ["ssh", f"{env['DEPLOY_USER']}@{env['DEPLOY_HOST']}", "bash", "-s"]
    print(f"+ ssh {env['DEPLOY_USER']}@{env['DEPLOY_HOST']} <deploy script>")
    subprocess.run(ssh_cmd, input=remote_cmd, text=True, check=True)

    print(f"deploy complete: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
