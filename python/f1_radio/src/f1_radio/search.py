"""Search over the indexed transcript corpus (Phase 4.3) -- the payoff of
embedding it in the first place: semantic search ("driver complaining about
front tyres" finds a relevant clip with none of those exact words),
narrowed by structured filters (driver, session, lap, track status, date
range) and/or a literal keyword substring, each result carrying the
transcript, clip metadata, confidence and a similarity score.

`SearchIndex` is the same Postgres/Memory split `f1_radio.store.
TranscriptStore` already makes: `PostgresSearchIndex` runs pgvector's
`<=>` cosine-distance operator server-side; `MemorySearchIndex` does the
same ranking in plain Python over an already-loaded list of
`TranscriptRecord`s, so the ranking/filter-composition logic has a real
unit test with no database (CLAUDE.md's "DB is a sink, fully testable with
no database present" discipline, extended here to a read path).

Not built here (CLAUDE.md's Phase 4 invariant: "Search is served through
f1_api like everything else; the frontend never touches the transcript
store or an embedding model directly"): an HTTP route. This module -- and
`f1-radio search` on top of it -- is the direct-access predecessor a later
prompt wraps, the same relationship `f1_radio.quality_report` had to a
future `f1_api` route before this one existed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

from f1_domain import SessionType
from f1_radio.embeddings import EmbeddingBackend, embed_texts
from f1_radio.store import ALL_COLUMNS, TABLE, TranscriptRecord, record_from_row, vector_literal


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Structured narrowing applied BEFORE (Postgres) or alongside (Memory)
    similarity ranking. `keyword` is a literal case-insensitive substring
    match on the transcript text -- the "keyword" half of "hybrid semantic +
    keyword". There is no `team` filter: a radio clip carries a driver
    number, not a team, and this package has no driver->team roster to join
    against -- documented here rather than silently ignored if asked for.
    """

    driver: str | None = None
    season: int | None = None
    round: int | None = None
    session_type: SessionType | None = None
    lap_number: int | None = None
    track_status: str | None = None
    captured_after: datetime | None = None
    captured_before: datetime | None = None
    keyword: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    record: TranscriptRecord
    """The transcript plus its clip metadata and Phase 4.3 race context
    (`record.lap_number`/`.position`/`.track_status`) in one object -- what
    the deliverable calls "the transcript, clip metadata, confidence, and a
    similarity score" (`record.confidence` is the transcription confidence
    from Phase 4.2; `similarity` below is this search's own score)."""
    similarity: float
    """Cosine similarity in [-1, 1] (typically [0, 1] for sentence
    embeddings) between the query and this result's embedding. 0.0 for a
    pure-filter query with no semantic component (no query text given) --
    never used to imply "no match," since a filter-only search has no
    similarity concept at all."""


class SearchIndex(Protocol):
    """What `search_transcripts` needs from wherever the corpus lives --
    the same narrow-protocol split `f1_kernel.sources.EventSource` and
    `f1_radio.store.TranscriptStore` already use in this project."""

    def search(
        self, query_embedding: list[float] | None, *, filters: SearchFilters, limit: int
    ) -> list[SearchResult]: ...


def search_transcripts(
    query: str,
    *,
    index: SearchIndex,
    embedding_backend: EmbeddingBackend | None,
    filters: SearchFilters | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    """The one entry point: embeds `query` (unless it's blank, or no
    `embedding_backend` was given -- a pure structured/keyword search with
    no semantic ranking) and delegates ranking/filtering to `index`."""
    resolved_filters = filters if filters is not None else SearchFilters()
    query_embedding: list[float] | None = None
    if query.strip() and embedding_backend is not None:
        [query_embedding] = embed_texts([query], embedding_backend)
    return index.search(query_embedding, filters=resolved_filters, limit=limit)


def _matches_filters(record: TranscriptRecord, filters: SearchFilters) -> bool:
    if filters.driver is not None and record.driver != filters.driver:
        return False
    if filters.season is not None and record.session_key.year != filters.season:
        return False
    if filters.round is not None and record.session_key.round != filters.round:
        return False
    if filters.session_type is not None and record.session_key.session_type != filters.session_type:
        return False
    if filters.lap_number is not None and record.lap_number != filters.lap_number:
        return False
    if filters.track_status is not None and record.track_status != filters.track_status:
        return False
    if filters.captured_after is not None and (
        record.captured_at is None or record.captured_at < filters.captured_after
    ):
        return False
    if filters.captured_before is not None and (
        record.captured_at is None or record.captured_at > filters.captured_before
    ):
        return False
    if filters.keyword is not None:
        haystack = (record.transcript_text or "").casefold()
        if filters.keyword.casefold() not in haystack:
            return False
    return True


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemorySearchIndex:
    """Ranks an already-loaded list of `TranscriptRecord`s in plain Python
    -- no database, no pgvector. Filters first (same semantics as the SQL
    `WHERE` `PostgresSearchIndex` builds), then ranks by cosine similarity
    when a query embedding was given; falls back to newest-first when it
    wasn't, so a pure-filter search still returns a stable, sensible order.
    A record with no stored embedding never matches a semantic query (it
    has nothing to compare against) but is still eligible for a pure-filter
    search.
    """

    def __init__(self, records: Iterable[TranscriptRecord]) -> None:
        self._records = list(records)

    def search(
        self, query_embedding: list[float] | None, *, filters: SearchFilters, limit: int
    ) -> list[SearchResult]:
        candidates = [r for r in self._records if _matches_filters(r, filters)]
        if query_embedding is not None:
            scored = [
                SearchResult(record=r, similarity=_cosine_similarity(query_embedding, r.embedding))
                for r in candidates
                if r.embedding is not None
            ]
            scored.sort(key=lambda result: result.similarity, reverse=True)
        else:
            ranked = sorted(candidates, key=lambda r: r.captured_at or datetime.min, reverse=True)
            scored = [SearchResult(record=r, similarity=0.0) for r in ranked]
        return scored[:limit]


class PostgresSearchIndex:
    """Runs the same search server-side: structured filters as SQL `WHERE`
    clauses, ranked by pgvector's `<=>` cosine-DISTANCE operator when a
    query embedding is given (`1 - distance` reported as `similarity`,
    a true cosine similarity since `f1_radio.embeddings` stores unit-norm
    vectors). No ANN index is assumed (see migration 003's own docstring for
    why a brute-force scan is fine at this corpus size).
    """

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def search(
        self, query_embedding: list[float] | None, *, filters: SearchFilters, limit: int
    ) -> list[SearchResult]:
        conditions: list[sql.Composable] = []
        params: list[Any] = []
        if filters.driver is not None:
            conditions.append(sql.SQL("driver = %s"))
            params.append(filters.driver)
        if filters.season is not None:
            conditions.append(sql.SQL("session_year = %s"))
            params.append(filters.season)
        if filters.round is not None:
            conditions.append(sql.SQL("session_round = %s"))
            params.append(filters.round)
        if filters.session_type is not None:
            conditions.append(sql.SQL("session_type = %s"))
            params.append(filters.session_type.value)
        if filters.lap_number is not None:
            conditions.append(sql.SQL("lap_number = %s"))
            params.append(filters.lap_number)
        if filters.track_status is not None:
            conditions.append(sql.SQL("track_status = %s"))
            params.append(filters.track_status)
        if filters.captured_after is not None:
            conditions.append(sql.SQL("captured_at >= %s"))
            params.append(filters.captured_after)
        if filters.captured_before is not None:
            conditions.append(sql.SQL("captured_at <= %s"))
            params.append(filters.captured_before)
        if filters.keyword is not None:
            conditions.append(sql.SQL("transcript_text ILIKE %s"))
            params.append(f"%{filters.keyword}%")

        where = sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("TRUE")
        col_ident = sql.SQL(", ").join(sql.Identifier(c) for c in ALL_COLUMNS)

        if query_embedding is not None:
            vec_literal = vector_literal(query_embedding)
            stmt = sql.SQL(
                "SELECT {cols}, 1 - (embedding <=> %s) AS similarity FROM {table} "
                "WHERE ({where}) AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s LIMIT %s"
            ).format(cols=col_ident, table=sql.Identifier(TABLE), where=where)
            exec_params: list[Any] = [vec_literal, *params, vec_literal, limit]
        else:
            stmt = sql.SQL(
                "SELECT {cols}, NULL::double precision AS similarity FROM {table} "
                "WHERE {where} ORDER BY captured_at DESC NULLS LAST LIMIT %s"
            ).format(cols=col_ident, table=sql.Identifier(TABLE), where=where)
            exec_params = [*params, limit]

        with self._conn.cursor() as cur:
            cur.execute(stmt, exec_params)
            rows = cur.fetchall()

        results: list[SearchResult] = []
        for row in rows:
            *record_columns, similarity = row
            record = record_from_row(tuple(record_columns))
            results.append(
                SearchResult(
                    record=record, similarity=float(similarity) if similarity is not None else 0.0
                )
            )
        return results
