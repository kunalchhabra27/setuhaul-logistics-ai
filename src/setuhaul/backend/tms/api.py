"""FastAPI routes for the TMS backend."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/tms", tags=["tms"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return a lightweight status payload for TMS smoke checks.

    Example:
    `GET /tms/health`
    """
    return {"status": "ok", "system": "tms"}
