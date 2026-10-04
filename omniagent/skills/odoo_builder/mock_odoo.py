"""Explicit in-memory Odoo substitute for local demos and offline tests."""

from __future__ import annotations

import threading
from typing import Any, Dict, List


class InMemoryOdooClient:
    """Small stateful fake. It is never selected implicitly by OdooBuilderSkill."""

    def __init__(self, pages: List[Dict[str, Any]] | None = None, leads: List[Dict[str, Any]] | None = None):
        self.pages = [dict(row) for row in (pages or [])]
        self.leads = [dict(row) for row in (leads or [])]
        self._next_id = max([int(row.get("id", 0)) for row in self.leads] + [0]) + 1
        self._lock = threading.Lock()

    def list_pages(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.pages[:limit]]

    def search_leads(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        needle = query.casefold()
        matches = [
            row for row in self.leads
            if any(needle in str(row.get(field, "")).casefold() for field in ("name", "email_from", "phone"))
        ]
        return [dict(row) for row in matches[:limit]]

    def create_lead(self, values: Dict[str, str]) -> int:
        with self._lock:
            lead_id = self._next_id
            self._next_id += 1
            self.leads.append({"id": lead_id, **values})
            return lead_id

