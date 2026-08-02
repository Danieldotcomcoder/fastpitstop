// The frontend only ever talks to `f1_api` over fetch/WebSocket (Phase 3
// invariant — never Postgres, never the kernel directly), so there is
// nothing here a server needs to render: every route is client-rendered
// against `adapter-static`'s SPA fallback.
export const ssr = false;
