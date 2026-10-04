"""Quota-aware provider failover for user-configured LLM routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import email.utils
import threading
import time
from typing import Any, Dict, FrozenSet, List, Optional

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

    def __post_init__(self) -> None:
        if not self.route_id or len(self.route_id) > 100:
            raise ValueError("route_id must be non-empty text up to 100 characters.")
        if self.cost_tier not in {"free", "prepaid", "paid"}:
            raise ValueError("cost_tier must be free, prepaid, or paid.")


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
        now = time.monotonic()
        candidates = [route for route in configured if self._cooldown_remaining(route.route_id, now) <= 0]
        if not candidates:
            nearest = min(self._cooldown_remaining(route.route_id, now) for route in configured)
            raise ProviderPoolError(f"All allowed provider routes are cooling down; retry in about {max(1, int(nearest))} seconds.")
        attempted: List[str] = []
        errors: List[str] = []
        for route in candidates[: self.max_attempts]:
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

    def status(self) -> List[Dict[str, Any]]:
        now = time.monotonic()
        result = []
        for route in self.routes:
            result.append({
                "route_id": route.route_id,
                "provider": route.provider.provider_name,
                "model": route.provider.model,
                "cost_tier": route.cost_tier,
                "enabled": route.enabled,
                "eligible_by_budget": route.cost_tier in self.allowed_cost_tiers,
                "cooldown_seconds": round(self._cooldown_remaining(route.route_id, now), 1),
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

