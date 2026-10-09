"""Cross-cutting concerns: security (bearer auth) and observability (correlation
id + one structured JSON log line per request). Both are contract-level, not
afterthoughts."""
from __future__ import annotations

import json
import sys
import time
from typing import Optional
from uuid import uuid4

from fastapi import Header, Request

from .problem import ProblemException, problem


async def require_bearer(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
    """Security by default: reject any request without a bearer token. A real
    deployment verifies the JWT against the OIDC issuer; here we enforce presence
    so the contract's security scheme is exercised."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ProblemException(
            problem("unauthorized", "Unauthorized", 401, "A valid bearer token is required", request.url.path)
        )


async def observability(request: Request, call_next):
    """Propagate a correlation id and emit the RED signals (status, duration)."""
    correlation_id = request.headers.get("x-correlation-id") or uuid4().hex
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["x-correlation-id"] = correlation_id
    sys.stdout.write(
        json.dumps(
            {
                "level": "error" if response.status_code >= 500 else "info",
                "msg": "http_request",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "durationMs": duration_ms,
                "correlationId": correlation_id,
            }
        )
        + "\n"
    )
    return response
