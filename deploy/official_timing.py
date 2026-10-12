"""F1's official end-of-session timing for a session, as JSON: what the
timing tower must show once the session is over (ROADMAP W4, "every tower
field checked against the official timing").

From the backfill's `results` (the official classification) and `laps`
(F1's timing archive, through FastF1), per driver:

- `position`: the official classified order; `classified` is false for a
  car that retired, was disqualified or did not start.
- `laps`: laps completed.
- `last_lap_s`: the time of the driver's last timed lap.
- `best_lap_s`: the driver's fastest lap, deleted laps excluded, as the
  official timing shows it.
- `stops`: pit stops made: the stints begun after the first lap.
- `compound`: the tyre the driver finished on.

    uv run python deploy/official_timing.py 2024-1-race 2024-6-race
    uv run python deploy/official_timing.py --historical /app/data/historical 2026-17-race

Read by `apps/web/e2e-real/tower.spec.ts`; also runnable on the VM to check
a live-ingested session's tower against the archive.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from f1_domain import SessionKey, SessionType
from f1_historical_adapter.reader import session_directory

_TYPES = {t.value: t for t in SessionType}


def _key(text: str) -> SessionKey:
    year, round_number, session_type = text.split("-", 2)
    return SessionKey(year=int(year), round=int(round_number), session_type=_TYPES[session_type])


def _seconds(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value):
        return None
    return round(pd.Timedelta(value).total_seconds(), 3)


def official_timing(historical: Path, key: SessionKey) -> dict[str, dict[str, Any]]:
    directory = session_directory(historical, key)
    results = pd.read_parquet(directory / "results.parquet")
    laps = pd.read_parquet(directory / "laps.parquet")
    out: dict[str, dict[str, Any]] = {}
    for rec in results.to_dict("records"):
        code = str(rec["Abbreviation"])
        mine = laps[laps["Driver"] == code].sort_values("LapNumber")
        timed = mine[mine["LapTime"].notna()]
        valid = timed[~timed["Deleted"].fillna(False).astype(bool)] if "Deleted" in timed else timed
        classified = str(rec.get("ClassifiedPosition") or "").isdigit()
        firsts = mine.dropna(subset=["Stint"]).groupby("Stint")["LapNumber"].min()
        out[code] = {
            "number": str(rec["DriverNumber"]),
            "position": int(rec["Position"]) if not pd.isna(rec["Position"]) else None,
            "classified": classified,
            "status": str(rec.get("Status") or ""),
            "laps": int(rec["Laps"]) if not pd.isna(rec.get("Laps")) else None,
            "last_lap_s": _seconds(timed["LapTime"].iloc[-1]) if len(timed) else None,
            "best_lap_s": _seconds(valid["LapTime"].min()) if len(valid) else None,
            "stops": int((firsts > 1).sum()) if len(firsts) else None,
            "compound": str(mine["Compound"].dropna().iloc[-1]).lower()
            if mine["Compound"].notna().any()
            else None,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keys", nargs="+", help="session keys like 2024-1-race")
    parser.add_argument("--historical", type=Path, default=Path("data/historical"))
    args = parser.parse_args()
    timing = {text: official_timing(args.historical, _key(text)) for text in args.keys}
    print(json.dumps(timing, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
