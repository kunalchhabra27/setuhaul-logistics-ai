"""SetuHaul FastAPI application entry point."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from setuhaul.backend.checkin_portal.api import router as checkin_portal_router
from setuhaul.backend.dock_scheduler.api import router as dock_scheduler_router
from setuhaul.backend.driver_chat_eta.api import (
    install_exception_handlers as install_driver_chat_eta_exception_handlers,
    router as driver_chat_eta_router,
)
from setuhaul.backend.tms.api import install_exception_handlers, router as tms_router
from setuhaul.infrastructure.logging import configure_logging
from setuhaul.infrastructure.observability import RequestObservabilityMiddleware
from setuhaul.infrastructure.telemetry import initialize_telemetry

configure_logging(os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="SetuHaul Backend", version="0.1.0")
app.add_middleware(RequestObservabilityMiddleware)
initialize_telemetry(app)
frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
api_prefix = "/api/v1"
app.include_router(tms_router, prefix=api_prefix)
install_exception_handlers(app)

app.include_router(dock_scheduler_router, prefix=api_prefix)
app.include_router(checkin_portal_router, prefix=api_prefix)
app.include_router(driver_chat_eta_router, prefix=api_prefix)
install_driver_chat_eta_exception_handlers(app)

@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Return a simple health response for local smoke checks."""
    return {"status": "ok", "service": "tms"}
