"""RFC 7807 problem details -- the single error shape for the whole API."""
from __future__ import annotations

from typing import Optional

from fastapi.responses import JSONResponse

BASE = "https://api.product.example.com/problems"
MEDIA_TYPE = "application/problem+json"


def problem(kind: str, title: str, status: int, detail: str, instance: str) -> dict:
    return {
        "type": f"{BASE}/{kind}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }


class ProblemException(Exception):
    """Raised by handlers/dependencies to emit a problem+json response."""

    def __init__(self, body: dict) -> None:
        self.body = body
        super().__init__(body.get("title", "Problem"))


def problem_response(body: dict, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(
        status_code=body["status"],
        content=body,
        media_type=MEDIA_TYPE,
        headers=headers,
    )
