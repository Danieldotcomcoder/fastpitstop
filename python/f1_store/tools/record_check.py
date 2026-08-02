"""`just record-check` — a pre-flight for a live recording session, run an
hour before every race weekend per CLAUDE.md's own deploy prompt ("a missed
race weekend is a permanent gap in the dataset"). Checks the three things
that would otherwise fail silently mid-session: disk space, database
reachability, and that the recordings path is actually writable — not just
present.

Lives under f1_store/tools/ (alongside backup.py/restore.py) rather than
python/f1_ingest/ deliberately: the recorder itself (f1_ingest) must not
depend on f1_store — Phase 0 records raw messages only and knows nothing
about the DB (CLAUDE.md) — but this ops script legitimately checks both,
and needs to ship inside python/Dockerfile's image (which copies the whole
python/ tree) so it's runnable in prod via
`docker compose run --rm worker python python/f1_store/tools/record_check.py`
against the real mounted volume, not just from a host checkout.

    uv run python python/f1_store/tools/record_check.py [--out data/recordings] [--min-free-gb 5]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from f1_store.config import connect, dsn_from_env

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_DIR = Path("data/recordings")
DEFAULT_MIN_FREE_GB = 5.0


def check_disk_space(path: Path, min_free_gb: float) -> tuple[bool, str]:
    path.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(path).free / (1024**3)
    ok = free_gb >= min_free_gb
    return ok, f"{free_gb:.1f} GiB free at {path} (need >= {min_free_gb:.1f} GiB)"


def check_writable(path: Path) -> tuple[bool, str]:
    probe = path / f".record-check-{uuid.uuid4().hex}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"{path} is not writable: {exc}"
    return True, f"{path} is writable"


def check_db(dsn: str | None) -> tuple[bool, str]:
    if not dsn:
        return (
            False,
            "F1_STORE_DSN is not set — recordings will still be written, but nothing ingests them",
        )
    try:
        with connect(dsn, autocommit=True) as conn:
            conn.execute("SELECT 1", prepare=False)
    except psycopg.Error as exc:
        return False, f"database unreachable: {exc}"
    return True, "database reachable"


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB)
    args = parser.parse_args()

    checks = [
        check_disk_space(args.out, args.min_free_gb),
        check_writable(args.out),
        check_db(dsn_from_env()),
    ]

    all_ok = True
    for ok, message in checks:
        print(f"{'OK  ' if ok else 'FAIL'} {message}")
        all_ok = all_ok and ok

    if not all_ok:
        print("\nrecord-check FAILED — fix the above before the session starts", file=sys.stderr)
        return 1
    print("\nrecord-check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
