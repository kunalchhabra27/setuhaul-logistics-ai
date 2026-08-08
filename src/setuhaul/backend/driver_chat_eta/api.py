"""FastAPI routes for the Driver Chat / ETA backend."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/driver-chat-eta", tags=["driver-chat-eta"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return a lightweight status payload for driver-chat smoke checks.

    Example:
    `GET /driver-chat-eta/health`
    """
    return {"status": "ok", "system": "driver-chat-eta"}
