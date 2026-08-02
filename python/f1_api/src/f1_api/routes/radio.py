"""The REST surface for Phase 4.4: the team-radio corpus, served through
`f1_api` exactly like everything else — CLAUDE.md's Phase 4 invariant that
the frontend never touches the transcript store or an embedding model
directly. Three routes:

    GET /radio/search              ranked, context-enriched search results
    GET /sessions/{key}/radio      every clip for one session, time-ordered
    GET /radio/{clip_id}           one clip: transcript, segments, context, audio URL

`/radio/search` is registered before `/radio/{clip_id}` — FastAPI matches
path operations in registration order, so the literal `/radio/search` must
be tried before the catch-all `/radio/{clip_id}` or a search request would
be (mis)matched as a clip lookup with `clip_id="search"`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from f1_api.convert import radio_clip_out, radio_search_result_out
from f1_api.radio import EmbeddingBackendFactoryDep, RadioStoreDep, SearchIndexDep, parse_clip_id
from f1_api.routes.sessions import SessionKeyDep, SettingsDep
from f1_api.schemas import RadioClipOut, RadioSearchResultOut
from f1_api.sessions import parse_session_key, resolve_session
from f1_api.sessions import session_key_path as _session_key_path
from f1_domain import SessionKey, TeamRadioClip
from f1_radio.search import SearchFilters, search_transcripts

router = APIRouter(tags=["radio"])


def _not_found(session_key: SessionKey) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"no session {_session_key_path(session_key)} in the historical backfill "
        "or (if configured) the live store",
    )


def get_clip_id(clip_id: str) -> tuple[SessionKey, str | None, datetime | None]:
    """Path-param dependency, mirroring `routes.sessions.get_session_key`:
    422s on a malformed `clip_id` rather than letting a `ValueError` 500."""
    try:
        return parse_clip_id(clip_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


ClipIdDep = Annotated[tuple[SessionKey, str | None, datetime | None], Depends(get_clip_id)]


@router.get("/radio/search", response_model=list[RadioSearchResultOut])
def search_radio(
    index: SearchIndexDep,
    embedding_backend_factory: EmbeddingBackendFactoryDep,
    q: Annotated[
        str, Query(description="Free-text query; blank runs a pure structured/keyword search")
    ] = "",
    driver: Annotated[
        str | None, Query(description="Restrict to one driver number, e.g. 44")
    ] = None,
    session: Annotated[
        str | None, Query(description="Restrict to one session, e.g. 2024-1-race")
    ] = None,
    from_: Annotated[
        datetime | None, Query(alias="from", description="captured_at lower bound (inclusive)")
    ] = None,
    to: Annotated[datetime | None, Query(description="captured_at upper bound (inclusive)")] = None,
    lap: Annotated[int | None, Query(description="Restrict to one lap number")] = None,
    track_status: Annotated[
        str | None, Query(description="Restrict to one track status at capture time, e.g. sc/green")
    ] = None,
    keyword: Annotated[
        str | None, Query(description="Require this literal substring in the transcript text")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> list[RadioSearchResultOut]:
    """Semantic + structured search over `radio_transcripts` — `f1_radio.
    search_transcripts` does the ranking, this route only reshapes the
    result and resolves `session=` into `SearchFilters`' season/round/type.
    An empty list, not a 500, when the corpus is unavailable (`F1_STORE_DSN`
    unset or unreachable) — the same "DB is a sink, never a dependency"
    degrade `f1_api.sessions` already applies to historical/live sessions."""
    if index is None:
        return []
    season = round_number = None
    session_type = None
    if session is not None:
        try:
            parsed = parse_session_key(session)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        season, round_number, session_type = parsed.year, parsed.round, parsed.session_type
    filters = SearchFilters(
        driver=driver,
        season=season,
        round=round_number,
        session_type=session_type,
        lap_number=lap,
        track_status=track_status,
        captured_after=from_,
        captured_before=to,
        keyword=keyword,
    )
    # The factory is only ever CALLED for a non-blank query — see
    # `EmbeddingBackendFactoryDep`'s own docstring for why resolving it is
    # cheap but invoking it (a real model load, in production) is not.
    embedding_backend = embedding_backend_factory() if q.strip() else None
    results = search_transcripts(
        q, index=index, embedding_backend=embedding_backend, filters=filters, limit=limit
    )
    return [radio_search_result_out(result) for result in results]


@router.get("/sessions/{key}/radio", response_model=list[RadioClipOut])
def get_session_radio(
    session_key: SessionKeyDep, settings: SettingsDep, radio_store: RadioStoreDep
) -> list[RadioClipOut]:
    """Every `TeamRadioClip` this session's own event stream carries, time-
    ordered, each joined to its transcript (if one exists yet). A session
    with no team radio at all (every fixture race in this repo, and any
    historical session `f1-radio fetch` hasn't been pointed at) returns `[]`
    — not an error, matching every other "nothing to report" state in this
    project."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        clips = [
            event
            for event in resolved.source.events(session_key, kinds=["team_radio_clip"])
            if isinstance(event, TeamRadioClip)
        ]
        clips.sort(key=lambda event: event.captured_at or event.ts_utc)
        return [
            radio_clip_out(
                event.session_key,
                event.driver,
                event.captured_at,
                event.audio_url,
                (
                    radio_store.get(event.session_key, event.driver, event.captured_at)
                    if radio_store is not None
                    else None
                ),
            )
            for event in clips
        ]


@router.get("/radio/{clip_id}", response_model=RadioClipOut)
def get_radio_clip(
    clip_id_parts: ClipIdDep, settings: SettingsDep, radio_store: RadioStoreDep
) -> RadioClipOut:
    """One clip by its opaque `clip_id` (`f1_api.radio.clip_id_path`):
    transcript, segments and race context from `radio_transcripts`, plus the
    playable `audio_url` — resolved best-effort from the originating
    session's own event stream (`None` if that session isn't resolvable, or
    never had a matching `TeamRadioClip` event — the transcript itself is
    still returned either way, per this project's "nothing silently
    dropped" discipline)."""
    session_key, driver, captured_at = clip_id_parts
    if radio_store is None:
        raise HTTPException(
            status_code=503, detail="radio corpus unavailable: F1_STORE_DSN is not configured"
        )
    transcript = radio_store.get(session_key, driver, captured_at)
    if transcript is None:
        raise HTTPException(status_code=404, detail="no transcript for this clip")

    audio_url: str | None = None
    with resolve_session(settings, session_key) as resolved:
        if resolved is not None:
            for event in resolved.source.events(session_key, kinds=["team_radio_clip"]):
                if (
                    isinstance(event, TeamRadioClip)
                    and event.driver == driver
                    and event.captured_at == captured_at
                ):
                    audio_url = event.audio_url
                    break

    return radio_clip_out(session_key, driver, captured_at, audio_url, transcript)


__all__ = ["router"]
