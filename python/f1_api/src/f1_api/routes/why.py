"""The REST surface for Phase 5.4: anomalies and grounded explanations,
served through `f1_api` exactly like everything else — CLAUDE.md's Phase 5
invariant that explanations pass the same product surface (and Delay Sync
gate, via the WebSocket) as every other derived value. Two routes:

    GET /sessions/{key}/anomalies       detected anomalies with salience (no prose)
    GET /sessions/{key}/story           the salience-ranked story, narrated in one pass
    GET /anomalies/{id}/explanation     grounded Explanation + cited evidence links

`/anomalies/{id}/explanation` is registered on this router (not under
`/sessions/{key}/...`) because the opaque `anomaly_id` already encodes the
session — the same shape `/radio/{clip_id}` uses for a corpus-scoped read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from f1_api.convert import anomaly_out, explanation_out
from f1_api.radio import SearchIndexDep
from f1_api.routes.sessions import SessionKeyDep, SettingsDep
from f1_api.schemas import AnomalyOut, ExplanationOut, StoryMomentOut
from f1_api.sessions import resolve_session
from f1_api.sessions import session_key_path as _session_key_path
from f1_api.why import (
    ExplanationStoreDep,
    NarrationModelDep,
    detect_session_anomalies,
    explain_session_anomaly,
    find_anomaly_by_why_id,
    most_salient,
    parse_anomaly_id,
    precompute_session_explanations,
    story_moments,
)
from f1_domain import SessionKey

router = APIRouter(tags=["why"])


def _not_found(session_key: SessionKey) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"no session {_session_key_path(session_key)} in the historical backfill "
        "or (if configured) the live store",
    )


def get_anomaly_path_id(anomaly_id: str) -> tuple[SessionKey, str]:
    """Path-param dependency, mirroring `routes.radio.get_clip_id`: 422s on
    a malformed `anomaly_id` rather than letting a `ValueError` 500."""
    try:
        return parse_anomaly_id(anomaly_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


AnomalyPathIdDep = Annotated[tuple[SessionKey, str], Depends(get_anomaly_path_id)]


@router.get("/sessions/{key}/anomalies", response_model=list[AnomalyOut])
def get_session_anomalies(
    session_key: SessionKeyDep,
    settings: SettingsDep,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "Keep only the N most salient anomalies. Omitted returns "
                "every one the detector found — a few hundred on a real "
                "race, which is the right answer for an analysis client "
                "and the wrong one for a UI list."
            ),
        ),
    ] = None,
) -> list[AnomalyOut]:
    """Every `f1_why.Anomaly` this session's own kernel report surfaces,
    already sorted most-salient-first. Detection only — no prose, no
    evidence bundle, no LLM call. An empty list (not an error) when the
    detector finds nothing worth explaining."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        _, anomalies = detect_session_anomalies(
            resolved.source,
            session_key,
            event_name=resolved.entry.event_name,
        )
        return [anomaly_out(session_key, anomaly) for anomaly in most_salient(anomalies, limit)]


@router.get("/sessions/{key}/story", response_model=list[StoryMomentOut])
def get_session_story(
    session_key: SessionKeyDep,
    settings: SettingsDep,
    model: NarrationModelDep,
    store: ExplanationStoreDep,
    radio_index: SearchIndexDep,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "How many moments to narrate. Defaults to the server's own "
                "F1_WHY_MAX_EXPLANATIONS budget."
            ),
        ),
    ] = None,
) -> list[StoryMomentOut]:
    """The race story: the moments worth a card (`f1_why.select_story`) with
    their grounded explanations, in race order, resolved in ONE pass over
    the session.

    The whole point of this route is that it detects once. Building the
    same list from `/anomalies` + one `/anomalies/{id}/explanation` per row
    re-ran `analyze_session` and `detect_anomalies` for every single row —
    seconds of duplicated work each, hundreds of times over. Detection is a
    per-session computation, so this is a per-session read; the per-anomaly
    route stays for a client that wants exactly one moment's citations."""
    budget = settings.max_explanations if limit is None else limit
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        scheduled = precompute_session_explanations(
            resolved.source,
            session_key,
            model,
            store,
            radio_index=radio_index,
            event_name=resolved.entry.event_name,
            max_explanations=budget,
        )
        # Race order, as `precompute_session_explanations` returns it: the
        # story follows the race (start, battles, neutralizations, finish).
        return [
            StoryMomentOut(
                anomaly=anomaly_out(session_key, item.anomaly),
                explanation=explanation_out(
                    session_key, item.anomaly, item.explanation, item.bundle
                ),
            )
            for item in scheduled
        ]


@router.get("/anomalies/{anomaly_id}/explanation", response_model=ExplanationOut)
def get_anomaly_explanation(
    anomaly_path: AnomalyPathIdDep,
    settings: SettingsDep,
    model: NarrationModelDep,
    store: ExplanationStoreDep,
    radio_index: SearchIndexDep,
) -> ExplanationOut:
    """One grounded, cited explanation for the anomaly named by the opaque
    `anomaly_id`. Uses `explain_cached` — never regenerates when the store
    already has this anomaly. `cited_evidence` carries lazy-fetch `href`s;
    the client loads full radio/telemetry detail on demand."""
    session_key, why_id = anomaly_path
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        report, anomalies = detect_session_anomalies(
            resolved.source,
            session_key,
            event_name=resolved.entry.event_name,
        )
        # The story merges incidents (a sector loss into its lap's pace loss,
        # a rival's lost lap into the overtake it caused) without changing
        # ids, so look there first: the explanation then matches the card.
        anomaly = find_anomaly_by_why_id(story_moments(anomalies, None), why_id)
        if anomaly is None:
            anomaly = find_anomaly_by_why_id(anomalies, why_id)
        if anomaly is None:
            raise HTTPException(status_code=404, detail="no anomaly with this id in the session")
        explanation, bundle = explain_session_anomaly(
            resolved.source,
            session_key,
            anomaly,
            model,
            store,
            report=report,
            radio_index=radio_index,
            event_name=resolved.entry.event_name,
        )
        return explanation_out(session_key, anomaly, explanation, bundle)


__all__ = ["router"]
