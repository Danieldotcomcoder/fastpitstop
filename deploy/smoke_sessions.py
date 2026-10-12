"""Smoke-check every session the archive lists (ROADMAP W8: "each one opens
and passes W0's smoke check").

For each session in `GET /sessions`, against a running API (production by
default):

- its REST reads answer: the session itself, its laps (at least one), its
  circuit, and for a race or sprint its story;
- its replay opens: `/stream/{key}?mode=replay&speed=0` sends the roster and
  a completed lap within `--stream-timeout` seconds, and is then closed. One
  session at a time, since an open replay holds a kernel.

    uv run python deploy/smoke_sessions.py
    uv run python deploy/smoke_sessions.py --api http://127.0.0.1:8000 --only 2024

Prints each failing session and a summary; exit status 1 on any failure.
Needs the network; not part of CI.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from typing import Any

import httpx
from websockets.sync.client import connect

STORY_TYPES = {"race", "sprint"}


def _rest(client: httpx.Client, key: str, session_type: str) -> list[str]:
    problems: list[str] = []
    paths = [f"/sessions/{key}", f"/sessions/{key}/laps", f"/sessions/{key}/circuit"]
    if session_type in STORY_TYPES:
        paths.append(f"/sessions/{key}/story")
    for path in paths:
        try:
            response = client.get(path)
        except httpx.HTTPError as exc:
            problems.append(f"{path}: {type(exc).__name__}")
            continue
        if response.status_code != 200:
            problems.append(f"{path}: HTTP {response.status_code}")
        elif path.endswith("/laps") and not response.json():
            problems.append(f"{path}: no laps")
    return problems


def _stream(ws_base: str, key: str, timeout_s: float) -> list[str]:
    wanted = {"driver_list_updated", "lap_completed"}
    seen: set[str] = set()
    deadline = time.monotonic() + timeout_s
    try:
        with connect(f"{ws_base}/stream/{key}?mode=replay&speed=0", open_timeout=timeout_s) as ws:
            while not wanted <= seen:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                envelope: dict[str, Any] = json.loads(ws.recv(timeout=remaining))
                seen.add(str(envelope.get("kind")))
    except TimeoutError:
        pass
    except Exception as exc:  # any failure to open or read is the finding
        return [f"stream: {type(exc).__name__}: {exc}"[:200]]
    missing = sorted(wanted - seen)
    return [f"stream: no {', '.join(missing)} within {timeout_s:.0f}s"] if missing else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="https://fastpitstop.online/api")
    parser.add_argument("--only", type=int, nargs="*", help="seasons to check (default: all)")
    parser.add_argument("--stream-timeout", type=float, default=45.0)
    args = parser.parse_args()

    ws_base = "ws" + args.api.removeprefix("http")
    failures = 0
    by_type: Counter[str] = Counter()
    with httpx.Client(base_url=args.api, timeout=120) as client:
        sessions = client.get("/sessions").json()
        if args.only:
            sessions = [s for s in sessions if s["session_key"]["year"] in args.only]
        started = time.monotonic()
        for session in sessions:
            key, session_type = session["key"], session["session_key"]["session_type"]
            by_type[session_type] += 1
            problems = _rest(client, key, session_type) + _stream(ws_base, key, args.stream_timeout)
            if problems:
                failures += 1
                print(f"FAIL {key}: " + "; ".join(problems), flush=True)
        elapsed = time.monotonic() - started
    kinds = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items()))
    print(f"{len(sessions)} sessions ({kinds}) in {elapsed:.0f}s: {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
