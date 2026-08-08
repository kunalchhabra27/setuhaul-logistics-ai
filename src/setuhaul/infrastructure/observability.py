"""HTTP request correlation and structured access logging."""

from __future__ import annotations

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger("setuhaul.http")


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    """Attach a request ID and emit one safe structured access log."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if supplied and len(supplied) <= 128 else str(uuid4())
        request.state.request_id = request_id
        started = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            logger.info(
                "request_completed",
                extra={
                    "structured_fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "endpoint": route_path,
                        "status": status,
                        "duration_ms": round((perf_counter() - started) * 1000, 2),
                    }
                },
            )
