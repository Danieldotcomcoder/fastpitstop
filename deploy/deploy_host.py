"""`just deploy-host` — deploy by building ON THE VM, with no registry and no
CI in the loop.

The other two paths both need a registry: `deploy.py` builds locally and
pushes to GHCR, and `deploy.py --no-build` rolls an image CI already pushed.
This one is for when neither is wanted — no GitHub repo, and a local machine
too slow (or too metered) to build multi-GB images. It streams this checkout
to the VM over SSH, builds both images there, and brings the stack up.

Required env (.env, same as deploy.py): DEPLOY_HOST, DEPLOY_USER, DEPLOY_PATH.
GHCR_NAMESPACE is still read, because compose.yml's `image:` keys name the
built images even when nothing is ever pushed.

    uv run python deploy/deploy_host.py             # sync, build, up
    uv run python deploy/deploy_host.py --no-sync   # rebuild what's there
    uv run python deploy/deploy_host.py --skip-build # just re-up

Set up key-based SSH first (`ssh-copy-id <user>@<host>`); otherwise every
step below prompts for a password separately.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = ["DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_PATH"]

# Everything the VM rebuilds or must never receive. `data/` is the big one:
# it holds recordings and the radio corpus, lives on the `pitwall-data`
# volume in production, and must not be clobbered by a laptop's copy.
EXCLUDES = [
    ".git",
    ".env.*-backup",
    "data",
    "node_modules",
    ".venv",
    ".svelte-kit",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "*.pyc",
    "test-results",
    "playwright-report",
]


def _require_env() -> dict[str, str]:
    values = {name: os.environ.get(name) for name in REQUIRED_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print(
            f"missing required env var(s): {', '.join(missing)} — see .env.example", file=sys.stderr
        )
        raise SystemExit(2)
    return {k: v for k, v in values.items() if v is not None}


def _ssh_target(env: dict[str, str]) -> str:
    return f"{env['DEPLOY_USER']}@{env['DEPLOY_HOST']}"


SSH_KEEPALIVE = [
    "-o",
    "ServerAliveInterval=20",
    "-o",
    "ServerAliveCountMax=15",
    "-o",
    "TCPKeepAlive=yes",
]
"""Keep a silent connection open across a long build step.

`docker build` can spend minutes inside one layer (exporting the API image
is the usual one) without writing a byte to stdout. With no keepalive the
connection is simply reset — the deploy then fails with ssh's exit 255 and
no build error at all, having actually completed most of the work. Five
minutes of silence tolerated, which is longer than any single step here.
"""


def _ssh_command(env: dict[str, str], *args: str) -> list[str]:
    return ["ssh", *SSH_KEEPALIVE, _ssh_target(env), *args]


def _run_remote(env: dict[str, str], script: str) -> None:
    cmd = _ssh_command(env, "bash", "-s")
    # Send BYTES, not text: subprocess's text mode rewrites newlines to
    # os.linesep on write, so from Windows the remote bash receives CRLF
    # and chokes on the trailing carriage return, reporting the option as
    # "pipefail: invalid option name".
    subprocess.run(cmd, input=script.encode(), check=True)


def _sync(env: dict[str, str]) -> None:
    """Stream the checkout to DEPLOY_PATH as a tarball over SSH.

    tar-over-ssh rather than rsync: rsync is not present on a stock Windows
    box, and this needs no third-party tooling on either end. `--delete`
    semantics are deliberately NOT emulated — a stale file on the host is
    less dangerous than a sync that can remove things.
    """
    path = env["DEPLOY_PATH"]
    print(f"+ syncing checkout -> {_ssh_target(env)}:{path}")
    _run_remote(env, f"set -euo pipefail\nmkdir -p {path}\n")

    tar_cmd = ["tar", "-czf", "-"]
    for pattern in EXCLUDES:
        tar_cmd += ["--exclude", pattern]
    tar_cmd += ["-C", str(REPO_ROOT), "."]

    ssh_cmd = _ssh_command(env, f"tar -xzf - -C {path}")
    tar = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
    assert tar.stdout is not None
    ssh = subprocess.Popen(ssh_cmd, stdin=tar.stdout)
    tar.stdout.close()
    ssh.communicate()
    tar.wait()
    if tar.returncode != 0 or ssh.returncode != 0:
        print(f"sync failed (tar={tar.returncode} ssh={ssh.returncode})", file=sys.stderr)
        raise SystemExit(1)


def _remote_script(path: str, *, build: bool) -> str:
    # `docker compose build` (not pull): compose.yml's `image:` keys just name
    # what the build is tagged as locally. Nothing is pushed, nothing pulled,
    # so no registry login is needed anywhere in this path.
    # `build api web`, not a bare `build`: compose.yml gives `api` and
    # `worker` the SAME `image:` name (they are one image differing only in
    # `command:`), and buildkit builds targets in parallel -- so a bare
    # build races two exports onto one tag and one of them dies with
    # "image ... already exists" while the other is CANCELED. Building
    # `worker` separately was pure duplicated work anyway.
    build_step = (
        "docker compose -f compose.yml -f compose.prod.yml build api web\n"
        if build
        else "echo 'skipping build'\n"
    )
    return (
        "set -euo pipefail\n"
        f"cd {path}\n"
        # A .env arriving from Windows with CRLF gives every value a trailing
        # carriage return -- which compose then bakes into DSNs, mount paths
        # and image tags. Normalise defensively; it is cheap and idempotent.
        "sed -i 's/\r$//' .env\n"
        f"{build_step}"
        "docker compose -f compose.yml -f compose.prod.yml up -d --wait\n"
        "docker compose -f compose.yml -f compose.prod.yml run --rm worker f1-store-migrate\n"
        "docker compose -f compose.yml -f compose.prod.yml ps\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-sync", action="store_true", help="skip the source sync")
    parser.add_argument("--skip-build", action="store_true", help="skip the remote build")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    env = _require_env()

    if not args.no_sync:
        _sync(env)

    print(f"+ ssh {_ssh_target(env)} <build + up>")
    _run_remote(env, _remote_script(env["DEPLOY_PATH"], build=not args.skip_build))
    print("deploy complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
