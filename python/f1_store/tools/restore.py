"""`just restore` — restore a `pg_dump -Fc` backup into a scratch database.

Deliberately never targets the live database by default: `--target-db` is
required and must not equal `POSTGRES_DB`, so restoring is always an
explicit, named action against a throwaway database you then inspect and
promote by hand if you actually mean to replace prod data. This is what
"an untested backup is not a backup" (CLAUDE.md's Phase-2-deploy prompt)
means in practice — the restore path has to actually run, not just exist.

    uv run python python/f1_store/tools/restore.py \
        --file backups/pitwall-dev-*.dump --target-db pitwall_restore_test
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]


def _compose_cmd(env: str, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(REPO_ROOT / "compose.yml"),
        "-f",
        str(REPO_ROOT / f"compose.{env}.yml"),
        *args,
    ]


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True, help="a pg_dump -Fc file")
    parser.add_argument(
        "--target-db",
        required=True,
        help="scratch database to restore into — must not be the live POSTGRES_DB",
    )
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    args = parser.parse_args()

    pg_user = os.environ.get("POSTGRES_USER", "pitwall")
    pg_db = os.environ.get("POSTGRES_DB", "pitwall")

    if args.target_db == pg_db:
        print(
            f"--target-db must not be the live database ({pg_db!r}) — "
            "pick a scratch name, e.g. pitwall_restore_test",
            file=sys.stderr,
        )
        return 2
    if not args.file.is_file():
        print(f"no such file: {args.file}", file=sys.stderr)
        return 2

    createdb_cmd = _compose_cmd(
        args.env, "exec", "-T", "db", "createdb", "-U", pg_user, args.target_db
    )
    print(f"running: {' '.join(createdb_cmd)}", file=sys.stderr)
    created = subprocess.run(createdb_cmd, check=False)
    if created.returncode != 0:
        print(f"createdb failed (exit {created.returncode})", file=sys.stderr)
        return created.returncode

    restore_cmd = _compose_cmd(
        args.env, "exec", "-T", "db", "pg_restore", "-U", pg_user, "-d", args.target_db
    )
    print(f"running: {' '.join(restore_cmd)} < {args.file}", file=sys.stderr)
    with args.file.open("rb") as f:
        restored = subprocess.run(restore_cmd, stdin=f, check=False)
    if restored.returncode != 0:
        print(f"pg_restore failed (exit {restored.returncode})", file=sys.stderr)
        return restored.returncode

    print(f"restored {args.file} into database {args.target_db!r}")
    print(
        f"inspect it, then drop it when done: "
        f"docker compose exec db dropdb -U {pg_user} {args.target_db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
