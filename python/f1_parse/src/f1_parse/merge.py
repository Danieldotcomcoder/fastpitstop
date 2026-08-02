"""`deep_merge`: fold one partial delta into a running snapshot.

Most live-timing topics send PARTIAL DELTAS, not full state: a TimingData
message contains only the fields that changed, nested under the same paths
as the full structure. A shallow `dict.update` at the top level would
replace a driver's whole entry with the two fields that changed — losing
everything else. This merge recurses instead.

Semantics (each is deliberate; tests pin all of them):

- dict into dict     -> recursive per-key merge; keys absent from the
                        delta are left untouched.
- `null` in delta    -> explicit reset: the key is KEPT with value None
                        (the feed said "this is now nothing" — that's
                        information, distinct from "no update").
- empty dict/list    -> container reset: replaces whatever was there.
- non-empty list     -> replaces outright (feed lists are snapshots, not
                        per-index deltas).
- dict of integer-
  string keys into a
  list               -> the feed's sparse-list encoding (e.g. `"Sectors":
                        {"2": {"Value": "31.2"}}` updating sector 3 only):
                        merged per index, padding unseen indices with None.
- scalar             -> replaces.

The result is always a NEW structure; `base` is never mutated (the
pipeline must stay a pure function of the record stream — CLAUDE.md,
Phase 1 invariants).
"""

from __future__ import annotations

from pydantic import JsonValue


def deep_merge(base: JsonValue, delta: JsonValue) -> JsonValue:
    """Return `base` with `delta` folded in, per the module's semantics."""
    if isinstance(delta, dict):
        if not delta:
            return {}
        if isinstance(base, dict):
            merged: dict[str, JsonValue] = dict(base)
            for key, value in delta.items():
                merged[key] = deep_merge(merged.get(key), value)
            return merged
        if isinstance(base, list) and _is_sparse_list_delta(delta):
            items: list[JsonValue] = list(base)
            for key, value in delta.items():
                index = int(key)
                while len(items) <= index:
                    items.append(None)
                items[index] = deep_merge(items[index], value)
            return items
        # No (or incompatible) base: build the structure fresh from the
        # delta alone, still recursing so nested values are copied.
        return {key: deep_merge(None, value) for key, value in delta.items()}
    if isinstance(delta, list):
        return [deep_merge(None, item) for item in delta]
    return delta


def _is_sparse_list_delta(delta: dict[str, JsonValue]) -> bool:
    return all(key.isdigit() for key in delta)
