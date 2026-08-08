"""FastAPI application entrypoint for SetuHaul."""

from __future__ import annotations

from fastapi import FastAPI

from setuhaul.backend.checkin_portal.api import router as checkin_portal_router
from setuhaul.backend.dock_scheduler.api import router as dock_scheduler_router
from setuhaul.backend.driver_chat_eta.api import router as driver_chat_eta_router
from setuhaul.backend.tms.api import router as tms_router

app = FastAPI(title="SetuHaul Backend", version="0.1.0")

app.include_router(tms_router)
app.include_router(dock_scheduler_router)
app.include_router(checkin_portal_router)
app.include_router(driver_chat_eta_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Return a simple health response for local smoke checks."""
    return {"status": "ok"}
