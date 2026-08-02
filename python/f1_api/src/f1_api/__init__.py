"""f1_api: Phase 3.1's FastAPI read model — the only thing the frontend
talks to.

Bridges the pure-Python analytics kernel (`f1_kernel`) and event store
(`f1_store`) to the TypeScript frontend over REST (point-in-time reads) and
a WebSocket envelope stream (`/stream/{key}`, live or replay-as-live). This
package adds no analytics of its own (CLAUDE.md's Phase 3 invariant) — every
number it returns is a kernel value, reshaped, never recomputed.

    from f1_api.main import create_app
    app = create_app()

See `docs/PHASE3.md` for the runbook.
"""

from f1_api.main import create_app

__all__ = ["create_app"]

__version__ = "0.0.1"
