"""Public, read-only, unauthenticated endpoints for Seedcast."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..services import public as service

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/snapshot")
def snapshot() -> dict[str, Any]:
    return service.snapshot_payload()


@router.get("/clusters/{public_id}")
def cluster(public_id: int) -> dict[str, Any]:
    detail = service.cluster_detail_payload(public_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="cluster not found in the live run")
    return detail
