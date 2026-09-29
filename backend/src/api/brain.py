"""
The owner's second brain (agentcenter and its Oracle) as a source of suggestions.

`GET /api/v1/brain/suggestions` lists what agentcenter holds that this platform can
act on: the Oracle's typed hypotheses (each opens as a prefilled analysis), its
review queue's next steps, and the learning radar's improvement targets, with each
source's reachability. `GET /api/v1/brain/suggestions/{hyp_id}/data` fetches a
hypothesis's data when its binding allows it. Read-only; see
src.connectors.agentcenter for configuration and the optional decision events.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.connectors import agentcenter
from src.middleware.rbac import guard

router = APIRouter(prefix="/api/v1/brain", tags=["Second brain"])


@router.get(
    "/suggestions",
    response_model=agentcenter.Suggestions,
    summary="Suggestions from agentcenter and the Oracle",
)
async def list_suggestions(
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/brain/suggestions")),
) -> agentcenter.Suggestions:
    return await agentcenter.suggestions()


@router.get(
    "/suggestions/{hyp_id}/data",
    response_model=agentcenter.BoundData,
    summary="A hypothesis's data, when this platform can fetch it",
)
async def suggestion_data(
    hyp_id: str,
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/brain/suggestions/{hyp_id}/data")),
) -> agentcenter.BoundData:
    try:
        return await agentcenter.bound_data(hyp_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except agentcenter.BindingUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
