"""SetuHaul FastAPI application entry point."""

from __future__ import annotations

import os

from fastapi import FastAPI

from setuhaul.backend.tms.api import install_exception_handlers, router as tms_router
from setuhaul.infrastructure.logging import configure_logging
from setuhaul.infrastructure.observability import RequestObservabilityMiddleware

configure_logging(os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="SetuHaul Transportation Management System", version="1.0.0")
app.add_middleware(RequestObservabilityMiddleware)
app.include_router(tms_router)
install_exception_handlers(app)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Return an unauthenticated process health indicator."""
    return {"status": "ok", "service": "tms"}
