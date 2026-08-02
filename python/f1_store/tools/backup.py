"""`just backup` — `pg_dump` the `db` compose service to a timestamped file.

Shells out to `docker compose exec` rather than a host-installed `pg_dump`,
so this works identically on a Windows dev machine (no postgres client
tools installed) and the Linux VPS: the only requirement is `docker compose`
itself. Uses `-Fc` (pg_dump's own compressed custom format) rather than
piping through `gzip`, which avoids relying on a Unix-only tool and the
binary-safety pitfalls of shell redirection differing between `sh` and
PowerShell (this script owns the file write, in binary mode, itself).

    uv run python python/f1_store/tools/backup.py [--env dev|prod] [--out-dir backups]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "backups")
    args = parser.parse_args()

    pg_user = os.environ.get("POSTGRES_USER", "pitwall")
    pg_db = os.environ.get("POSTGRES_DB", "pitwall")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out_dir / f"pitwall-{args.env}-{timestamp}.dump"

    cmd = [
        "docker",
        "compose",
        "-f",
        str(REPO_ROOT / "compose.yml"),
        "-f",
        str(REPO_ROOT / f"compose.{args.env}.yml"),
        "exec",
        "-T",
        "db",
        "pg_dump",
        "-U",
        pg_user,
        "-d",
        pg_db,
        "-Fc",
    ]
    print(f"running: {' '.join(cmd)}", file=sys.stderr)
    with out_path.open("wb") as f:
        result = subprocess.run(cmd, stdout=f, check=False)
    if result.returncode != 0:
        out_path.unlink(missing_ok=True)
        print(f"pg_dump failed (exit {result.returncode}); no backup written", file=sys.stderr)
        return result.returncode

    size = out_path.stat().st_size
    print(f"wrote {out_path} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
