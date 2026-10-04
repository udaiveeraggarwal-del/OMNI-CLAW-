"""Quota-aware provider failover for user-configured LLM routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import email.utils
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, FrozenSet, List, Optional

import requests

from omniagent.core.models import LLMResponse, Message, NetworkSecurityContext, ToolDefinition
from omniagent.core.providers.base import BaseLLMProvider


@dataclass(frozen=True)
class ProviderRoute:
    """One provider/model/key configuration. Never include its API key here."""

    route_id: str
    provider: BaseLLMProvider
    priority: int = 100
    cost_tier: str = "free"
    enabled: bool = True
    requests_per_minute: Optional[int] = None
    requests_per_day: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.route_id or len(self.route_id) > 100:
            raise ValueError("route_id must be non-empty text up to 100 characters.")
        if self.cost_tier not in {"free", "prepaid", "paid"}:
            raise ValueError("cost_tier must be free, prepaid, or paid.")
        if self.requests_per_minute is not None and not 1 <= self.requests_per_minute <= 1_000_000:
            raise ValueError("requests_per_minute must be between 1 and 1,000,000.")
        if self.requests_per_day is not None and not 1 <= self.requests_per_day <= 10_000_000:
            raise ValueError("requests_per_day must be between 1 and 10,000,000.")


class ProviderPoolError(RuntimeError):
    """All eligible provider routes failed or were unavailable."""


class ProviderPool(BaseLLMProvider):
    """Select providers in priority order and fail over on exhausted capacity.

    Only definite quota/rate-limit failures cause automatic failover by default.
    Responses that may have been generated before a network error are not
    silently retried, avoiding duplicate spend. Paid routes are disabled unless
    the host explicitly opts in.
    """

    def __init__(
        self,
        routes: List[ProviderRoute],
        *,
        allowed_cost_tiers: FrozenSet[str] = frozenset({"free"}),
        max_attempts: int = 4,
        default_cooldown_seconds: int = 60,
        max_cooldown_seconds: int = 900,
        failover_on_server_errors: bool = False,
    ) -> None:
        super().__init__(api_key="", model="provider-pool")
        if not routes:
            raise ValueError("ProviderPool needs at least one configured route.")
        ids = [route.route_id for route in routes]
        if len(set(ids)) != len(ids):
            raise ValueError("Provider route IDs must be unique.")
        if not allowed_cost_tiers or not allowed_cost_tiers <= {"free", "prepaid", "paid"}:
            raise ValueError("allowed_cost_tiers must contain free, prepaid, and/or paid.")
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20.")
        if not 1 <= default_cooldown_seconds <= 3600 or not default_cooldown_seconds <= max_cooldown_seconds <= 86400:
            raise ValueError("cooldown limits are invalid.")
        self.routes = sorted((route for route in routes if route.enabled), key=lambda route: (route.priority, route.route_id))
        self.allowed_cost_tiers = frozenset(allowed_cost_tiers)
        self.max_attempts = max_attempts
        self.default_cooldown_seconds = default_cooldown_seconds
        self.max_cooldown_seconds = max_cooldown_seconds
        self.failover_on_server_errors = failover_on_server_errors
        self._cooldowns: Dict[str, float] = {}
        self._failures: Dict[str, int] = {}
        self._last_errors: Dict[str, str] = {}
        self._minute_windows: Dict[str, Deque[float]] = {route.route_id: deque() for route in self.routes}
        self._daily_counts: Dict[str, tuple[date, int]] = {}
        self._lock = threading.RLock()

    @property
    def provider_name(self) -> str:
        return "pool"

    def format_tools(self, tools: List[ToolDefinition]) -> Any:
        return self._eligible_routes()[0].provider.format_tools(tools)

    def normalize_request(self, messages: List[Message], tools=None, temperature=0.7, max_tokens=None, **kwargs: Any) -> Dict[str, Any]:
        return self._eligible_routes()[0].provider.normalize_request(messages, tools, temperature, max_tokens, **kwargs)

    def normalize_response(self, raw_response: Dict[str, Any]) -> LLMResponse:
        return self._eligible_routes()[0].provider.normalize_response(raw_response)

    def set_security_context(self, ctx: NetworkSecurityContext) -> None:
        super().set_security_context(ctx)
        for route in self.routes:
            route.provider.set_security_context(ctx)

    def _eligible_routes(self) -> List[ProviderRoute]:
        routes = [route for route in self.routes if route.cost_tier in self.allowed_cost_tiers]
        if not routes:
            raise ProviderPoolError("No provider route is enabled within the configured cost policy.")
        return routes

    def _cooldown_remaining(self, route_id: str, now: Optional[float] = None) -> float:
        with self._lock:
            return max(0.0, self._cooldowns.get(route_id, 0.0) - (now if now is not None else time.monotonic()))

    def _mark_limited(self, route: ProviderRoute, retry_after: Optional[int] = None) -> None:
        with self._lock:
            failure_count = self._failures.get(route.route_id, 0) + 1
            self._failures[route.route_id] = failure_count
            delay = retry_after or min(
                self.default_cooldown_seconds * (2 ** (failure_count - 1)),
                self.max_cooldown_seconds,
            )
            self._cooldowns[route.route_id] = time.monotonic() + min(delay, self.max_cooldown_seconds)

    def _clear_failure(self, route: ProviderRoute) -> None:
        with self._lock:
            self._failures.pop(route.route_id, None)
            self._cooldowns.pop(route.route_id, None)
            self._last_errors.pop(route.route_id, None)

    def generate(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        configured = self._eligible_routes()
        candidates = self._reserve_available_routes(configured)
        if not candidates:
            status = self.status()
            remaining = [entry["cooldown_seconds"] for entry in status if entry["eligible_by_budget"]]
            nearest = min(remaining) if remaining else 0
            raise ProviderPoolError(
                "No provider route has quota available under the configured local budget."
                + (f" Earliest provider cooldown ends in about {max(1, int(nearest))} seconds." if nearest else "")
            )
        attempted: List[str] = []
        errors: List[str] = []
        for route in candidates[: self.max_attempts]:
            if not self._reserve_route(route):
                continue
            attempted.append(route.route_id)
            try:
                response = route.provider.generate(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            except Exception as exc:
                limited, retry_after, failover_allowed = self._classify_failure(exc)
                safe_reason = self._safe_reason(exc)
                with self._lock:
                    self._last_errors[route.route_id] = safe_reason
                if limited:
                    self._mark_limited(route, retry_after)
                errors.append(f"{route.route_id}: {safe_reason}")
                if not failover_allowed:
                    raise ProviderPoolError(
                        f"Provider route {route.route_id} failed; automatic retry was withheld because the outcome may be ambiguous."
                    ) from None
                continue
            self._clear_failure(route)
            return response
        raise ProviderPoolError(
            f"No eligible provider route completed the request. Tried {', '.join(attempted)}. "
            + "; ".join(errors)
        )

    def _classify_failure(self, exc: Exception) -> tuple[bool, Optional[int], bool]:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        headers = getattr(response, "headers", {}) or {}
        retry_after = _parse_retry_after(headers.get("Retry-After"))
        text = str(exc).casefold()
        quota_signal = any(term in text for term in ("rate limit", "rate_limit", "quota", "resource_exhausted", "resource exhausted", "too many requests"))
        if status == 429 or quota_signal:
            return True, retry_after, True
        if status in (408, 500, 502, 503, 504, 529) and self.failover_on_server_errors:
            return False, retry_after, True
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)) and self.failover_on_server_errors:
            return False, retry_after, True
        return False, None, False

    def _reserve_available_routes(self, configured: List[ProviderRoute]) -> List[ProviderRoute]:
        """List candidates under local ceilings; reservation occurs just before use."""
        now = time.monotonic()
        today = datetime.now(timezone.utc).date()
        candidates: List[ProviderRoute] = []
        with self._lock:
            for route in configured:
                if self._cooldowns.get(route.route_id, 0.0) > now:
                    continue
                minute_window = self._minute_windows.setdefault(route.route_id, deque())
                while minute_window and minute_window[0] <= now - 60:
                    minute_window.popleft()
                daily_date, daily_count = self._daily_counts.get(route.route_id, (today, 0))
                if daily_date != today:
                    daily_date, daily_count = today, 0
                if route.requests_per_minute is not None and len(minute_window) >= route.requests_per_minute:
                    continue
                if route.requests_per_day is not None and daily_count >= route.requests_per_day:
                    continue
                candidates.append(route)
            return candidates

    def _reserve_route(self, route: ProviderRoute) -> bool:
        now = time.monotonic()
        today = datetime.now(timezone.utc).date()
        with self._lock:
            if self._cooldowns.get(route.route_id, 0.0) > now:
                return False
            minute_window = self._minute_windows.setdefault(route.route_id, deque())
            while minute_window and minute_window[0] <= now - 60:
                minute_window.popleft()
            daily_date, daily_count = self._daily_counts.get(route.route_id, (today, 0))
            if daily_date != today:
                daily_date, daily_count = today, 0
            if route.requests_per_minute is not None and len(minute_window) >= route.requests_per_minute:
                return False
            if route.requests_per_day is not None and daily_count >= route.requests_per_day:
                return False
            minute_window.append(now)
            self._daily_counts[route.route_id] = (today, daily_count + 1)
            return True

    @staticmethod
    def _safe_reason(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status == 429:
            return "rate/quota limit"
        if status is not None:
            return f"HTTP {status}"
        if isinstance(exc, requests.Timeout):
            return "request timeout"
        if isinstance(exc, requests.ConnectionError):
            return "connection error"
        text = str(exc).casefold()
        if any(term in text for term in ("rate limit", "rate_limit", "quota", "resource_exhausted", "too many requests")):
            return "rate/quota limit"
        return type(exc).__name__

    def reset_cooldowns(self) -> None:
        with self._lock:
            self._cooldowns.clear()
            self._failures.clear()
            self._last_errors.clear()
            self._minute_windows = {route.route_id: deque() for route in self.routes}
            self._daily_counts.clear()

    def status(self) -> List[Dict[str, Any]]:
        result = []
        for route in self.routes:
            with self._lock:
                minute_window = self._minute_windows.setdefault(route.route_id, deque())
                now = time.monotonic()
                while minute_window and minute_window[0] <= now - 60:
                    minute_window.popleft()
                daily_date, daily_count = self._daily_counts.get(route.route_id, (datetime.now(timezone.utc).date(), 0))
                if daily_date != datetime.now(timezone.utc).date():
                    daily_count = 0
            result.append({
                "route_id": route.route_id,
                "provider": route.provider.provider_name,
                "model": route.provider.model,
                "cost_tier": route.cost_tier,
                "enabled": route.enabled,
                "eligible_by_budget": route.cost_tier in self.allowed_cost_tiers,
                "cooldown_seconds": round(self._cooldown_remaining(route.route_id, now), 1),
                "requests_last_minute": len(minute_window),
                "requests_today": daily_count,
                "recent_failure": self._last_errors.get(route.route_id),
            })
        return result


def _parse_retry_after(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return max(1, min(int(float(value)), 86400))
    except (TypeError, ValueError):
        try:
            moment = email.utils.parsedate_to_datetime(str(value))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return max(1, min(int((moment - datetime.now(timezone.utc)).total_seconds()), 86400))
        except (TypeError, ValueError, OverflowError):
            return None
