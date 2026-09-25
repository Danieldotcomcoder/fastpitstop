"""`just rollback` — redeploy the previous image tag on the VPS, via the
`.last-deployed-sha` file `deploy.py` writes on every deploy (before
overwriting `.env`'s `IMAGE_TAG`), so a rollback always has a target without
anyone needing to remember the prior SHA.

Required env: DEPLOY_HOST, DEPLOY_USER, DEPLOY_PATH (same as deploy.py).

    uv run python deploy/rollback.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = ["DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_PATH"]


def _require_env() -> dict[str, str]:
    values = {name: os.environ.get(name) for name in REQUIRED_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print(f"missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(2)
    return {k: v for k, v in values.items() if v is not None}


def _remote_rollback_command(deploy_path: str) -> str:
    return (
        "set -euo pipefail\n"
        f"cd {deploy_path}\n"
        "if [ ! -f .last-deployed-sha ]; then\n"
        '  echo "no .last-deployed-sha found — nothing to roll back to" >&2\n'
        "  exit 1\n"
        "fi\n"
        "PREV_TAG=$(cat .last-deployed-sha)\n"
        'sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$PREV_TAG/" .env\n'
        "docker compose pull\n"
        "docker compose -f compose.yml -f compose.prod.yml up -d --wait\n"
        'echo "rolled back to $PREV_TAG"\n'
    )


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    env = _require_env()
    remote_cmd = _remote_rollback_command(env["DEPLOY_PATH"])
    ssh_cmd = ["ssh", f"{env['DEPLOY_USER']}@{env['DEPLOY_HOST']}", "bash", "-s"]
    print(f"+ ssh {env['DEPLOY_USER']}@{env['DEPLOY_HOST']} <rollback script>")
    # Send BYTES, not text: subprocess's text mode rewrites newlines to
    # os.linesep on write, so from Windows the remote bash receives CRLF
    # and chokes on the trailing carriage return, reporting the option as
    # "pipefail: invalid option name".
    subprocess.run(ssh_cmd, input=remote_cmd.encode(), check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
