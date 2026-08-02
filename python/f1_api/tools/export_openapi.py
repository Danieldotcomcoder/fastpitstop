"""Regenerate the committed `openapi.json` — the typed-client source the
Phase 3 frontend generates against. Re-run after any response-model change:

    uv run python python/f1_api/tools/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from f1_api.main import create_app

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> int:
    app = create_app()
    schema = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
