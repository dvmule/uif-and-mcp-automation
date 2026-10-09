"""In-memory repository. A real domain swaps this for a database behind the same
interface; the handlers and tests do not change."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from .domain import Developer, DeveloperStatus, normalize_email


class EmailTakenError(Exception):
    pass


class DeveloperStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Developer] = {}
        self._by_email: dict[str, str] = {}

    def create(self, email: str, display_name: str) -> Developer:
        normalized = normalize_email(email)
        if normalized in self._by_email:
            raise EmailTakenError(normalized)
        developer = Developer(
            id=f"dev_{uuid4().hex[:12]}",
            email=normalized,
            displayName=display_name,
            status=DeveloperStatus.active,
            createdAt=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._by_id[developer.id] = developer
        self._by_email[normalized] = developer.id
        return developer

    def get(self, developer_id: str) -> Optional[Developer]:
        return self._by_id.get(developer_id)

    def list(self, page_size: int, cursor: Optional[str] = None) -> tuple[list[Developer], Optional[str]]:
        # Newest first; cursor is the id to start after (insertion order proxies time).
        ordered = list(self._by_id.values())[::-1]
        start = 0
        if cursor:
            ids = [d.id for d in ordered]
            start = ids.index(cursor) + 1 if cursor in ids else 0
        items = ordered[start : start + page_size]
        has_more = len(ordered) > start + page_size
        next_cursor = items[-1].id if has_more and items else None
        return items, next_cursor
