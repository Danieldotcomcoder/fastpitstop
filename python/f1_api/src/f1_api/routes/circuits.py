"""Circuit pages (ROADMAP S2): every shipped circuit, and one circuit's
measured figures with the races held there. Nothing here is a result, so
nothing needs a spoiler gate."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from f1_api.circuits import race_strategy, races_at
from f1_api.config import Settings, get_settings
from f1_api.convert import circuit_out
from f1_api.schemas import (
    CircuitProfileOut,
    CircuitRaceOut,
    CircuitSummaryOut,
    RaceStrategyOut,
    TrackOutlineOut,
    TyrePlanOut,
    TyreWearOut,
)
from f1_api.sessions import session_key_path
from f1_kernel import Circuit, list_circuits, load_circuit

router = APIRouter(tags=["circuits"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _summary(circuit: Circuit) -> CircuitSummaryOut:
    return CircuitSummaryOut(
        circuit_id=circuit.circuit_id,
        name=circuit.name,
        country=circuit.country,
        lap_distance_m=circuit.lap_distance_m,
        corners=len(circuit.corners),
        drs_zones=len(circuit.drs_zones),
        pit_loss_s=circuit.pit_loss_s,
        pit_loss_provenance=circuit.pit_loss_provenance,
        pit_loss_uncertainty_s=circuit.pit_loss_uncertainty_s,
        pit_loss_n_stops=circuit.pit_loss_n_stops,
        outline=(
            TrackOutlineOut.model_validate(circuit.outline.model_dump())
            if circuit.outline
            else None
        ),
    )


@router.get("/circuits", response_model=list[CircuitSummaryOut])
def get_circuits() -> list[CircuitSummaryOut]:
    """Every circuit this project ships measured data for."""
    return [_summary(load_circuit(circuit_id)) for circuit_id in list_circuits()]


@router.get("/circuits/{circuit_id}", response_model=CircuitProfileOut)
def get_circuit_profile(circuit_id: str, settings: SettingsDep) -> CircuitProfileOut:
    """A circuit page: its geometry, measured pit loss and tyre wear, and the
    races any source holds there with how each was run (strategy only)."""
    try:
        circuit = load_circuit(circuit_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"unknown circuit {circuit_id!r}") from error
    races: list[CircuitRaceOut] = []
    for entry in races_at(settings, circuit_id):
        key = entry.session_key
        # From the backfill whichever source serves the session: a race
        # ingested live is backfilled too once F1 archives it (None until).
        strategy = race_strategy(settings.historical_root, key)
        races.append(
            CircuitRaceOut(
                key=session_key_path(key),
                season=key.year,
                round=key.round,
                event_name=entry.event_name,
                strategy=(
                    RaceStrategyOut(
                        finishers=strategy.finishers,
                        stops={str(k): v for k, v in strategy.stops.items()},
                        plans=[
                            TyrePlanOut(compounds=list(plan), cars=cars)
                            for plan, cars in strategy.plans
                        ],
                    )
                    if strategy is not None
                    else None
                ),
            )
        )
    return CircuitProfileOut(
        **circuit_out(circuit).model_dump(),
        country=circuit.country,
        pit_loss_s=circuit.pit_loss_s,
        pit_loss_provenance=circuit.pit_loss_provenance,
        pit_loss_uncertainty_s=circuit.pit_loss_uncertainty_s,
        pit_loss_n_stops=circuit.pit_loss_n_stops,
        tyre_wear=[
            TyreWearOut(
                compound=compound,
                median_s_per_lap=wear.median_s_per_lap,
                good_fits=wear.good_fits,
                sessions=wear.sessions,
            )
            for compound, wear in circuit.tyre_wear.items()
        ],
        races=races,
    )
