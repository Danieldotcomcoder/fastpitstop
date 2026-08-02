"""The WebSocket half of the API: `/stream/{key}`.

`replay` paces a session's own `DomainEvent` stream back out using
`f1_replay`'s own timing model (`replay.py`); `websocket.py` is the route
that bridges that (synchronous, real-sleeping) generator into the async
WebSocket protocol, and also the (unit-tested-elsewhere-in-spirit-only,
per docs/PHASE0.md) `mode=live` DB-polling driver.
"""
