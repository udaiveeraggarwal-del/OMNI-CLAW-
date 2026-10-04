"""Ephemeral browser sessions with explicit egress and action controls.

Playwright browser contexts are non-persistent. By default the skill does not
allow direct egress, blocks private/reserved destinations, denies non-read
HTTP methods, blocks WebSockets and downloads, and requires a host-provided
approval callback before clicks. A network-level egress firewall remains
necessary to close DNS-rebinding and browser-process escape risks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit
import uuid

from omniagent.core.models import ToolResult
from omniagent.core.router import BaseTool
from omniagent.skills.base import BaseSkill
from omniagent.security.capabilities import HostCapabilityGrant


class BrowserPolicyError(RuntimeError):
    """Raised when a browser action violates the configured host policy."""


@dataclass(frozen=True)
class BrowserPolicy:
    """Host-owned limits. None of these controls can be changed by the model."""

    allow_direct_egress: bool = False
    proxy_ready: bool = False
    allowed_domains: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = (80, 443)
    allowed_methods: tuple[str, ...] = ("GET", "HEAD")
    allow_state_changing_requests: bool = False
    allow_unapproved_actions: bool = False
    max_sessions: int = 4
    session_ttl_seconds: int = 1800
    navigation_timeout_ms: int = 15000
    max_read_chars: int = 20000
    blocked_resource_types: tuple[str, ...] = ("image", "media", "font")

    def __post_init__(self) -> None:
        if not 1 <= self.max_sessions <= 32:
            raise ValueError("max_sessions must be between 1 and 32")
        if not 60 <= self.session_ttl_seconds <= 86400:
            raise ValueError("session_ttl_seconds must be between 60 and 86400")
        if not 1000 <= self.navigation_timeout_ms <= 120000:
            raise ValueError("navigation_timeout_ms must be between 1000 and 120000")
        if not 100 <= self.max_read_chars <= 100000:
            raise ValueError("max_read_chars must be between 100 and 100000")
        safe_methods = {"GET", "HEAD", "OPTIONS"}
        write_methods = {"POST", "PUT", "PATCH", "DELETE"}
        if not self.allowed_methods or any(
            method.upper() not in safe_methods | (write_methods if self.allow_state_changing_requests else set())
            for method in self.allowed_methods
        ):
            raise ValueError("allowed_methods contains an unsupported method or lacks write permission")
        normalized = []
        for domain in self.allowed_domains:
            host = domain.strip().lower().rstrip(".")
            if not host or "/" in host or ":" in host or "@" in host:
                raise ValueError(f"invalid allowed domain: {domain!r}")
            normalized.append(host.lstrip("."))
        object.__setattr__(self, "allowed_domains", tuple(normalized))
        object.__setattr__(self, "allowed_methods", tuple(method.upper() for method in self.allowed_methods))
        object.__setattr__(self, "allowed_ports", tuple(int(port) for port in self.allowed_ports))
        if any(not 1 <= port <= 65535 for port in self.allowed_ports):
            raise ValueError("allowed_ports must contain valid TCP port numbers")


@dataclass
class _BrowserSession:
    context: Any
    page: Any
    created_at: float
    last_used: float
    proxy_url: Optional[str]


class BrowserSessionManager:
    """Owns a bounded set of transient Playwright contexts."""

    _BLOCKED_SUFFIXES = (
        ".localhost",
        ".local",
        ".internal",
        ".test",
        ".invalid",
    )

    def __init__(
        self,
        network_security_context: Any = None,
        policy: BrowserPolicy | None = None,
        approval_callback: Optional[Callable[[Dict[str, str]], bool]] = None,
        capability_grant: Optional[HostCapabilityGrant] = None,
    ) -> None:
        self.network_security_context = network_security_context
        self.policy = policy or BrowserPolicy()
        self.approval_callback = approval_callback
        self.capability_grant = capability_grant
        self._playwright: Any = None
        self._browser: Any = None
        self._sessions: Dict[str, _BrowserSession] = {}
        self._lock = threading.RLock()

    def _egress(self) -> tuple[Optional[str], bool]:
        context = self.network_security_context
        privacy_enabled = bool(getattr(context, "enabled", False))
        if privacy_enabled:
            if not self.policy.proxy_ready:
                raise BrowserPolicyError(
                    "Privacy routing is enabled but the M2 proxy has not passed its readiness gate. "
                    "Browser traffic is blocked instead of sent directly."
                )
            proxy_url = getattr(context, "proxy_url", None)
            if not proxy_url:
                raise BrowserPolicyError("Privacy routing is enabled but no proxy URL is available.")
            return str(proxy_url), True
        host_allows_direct = bool(self.capability_grant and self.capability_grant.allows("network.direct"))
        if not self.policy.allow_direct_egress and not host_allows_direct:
            raise BrowserPolicyError(
                "Browser egress is disabled. Configure a ready privacy proxy or explicitly allow direct egress."
            )
        return None, False

    def _ensure_browser(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserPolicyError(
                "Playwright is not installed. Install OmniAgent with the browser extra, then install Chromium."
            ) from exc

        if self._playwright is None:
            self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--no-first-run",
                ],
            )
        except Exception as exc:
            raise BrowserPolicyError(
                "Chromium could not start. Install the Playwright Chromium browser and retry."
            ) from exc

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_used > self.policy.session_ttl_seconds
        ]
        for session_id in expired:
            session = self._sessions.pop(session_id)
            try:
                session.context.close()
            except Exception:
                pass

    def create_session(self) -> Dict[str, Any]:
        with self._lock:
            proxy_url, proxied = self._egress()
            self._cleanup_expired()
            if len(self._sessions) >= self.policy.max_sessions:
                raise BrowserPolicyError("The browser session limit has been reached.")
            self._ensure_browser()

            options: Dict[str, Any] = {
                "accept_downloads": False,
                "service_workers": "block",
            }
            if proxy_url:
                options["proxy"] = {"server": proxy_url}
            context = self._browser.new_context(**options)

            def route_handler(route: Any) -> None:
                request = route.request
                try:
                    self._validate_url(request.url, proxied=proxied)
                    allowed_methods = set(self.policy.allowed_methods)
                    if self.capability_grant and self.capability_grant.allows("browser.write"):
                        allowed_methods.update({"POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
                    elif self.policy.allow_state_changing_requests:
                        allowed_methods.update({"POST", "PUT", "PATCH", "DELETE"})
                    if request.method.upper() not in allowed_methods:
                        route.abort("blockedbyclient")
                    elif request.resource_type in self.policy.blocked_resource_types:
                        route.abort("blockedbyclient")
                    else:
                        route.continue_()
                except Exception:
                    try:
                        route.abort("blockedbyclient")
                    except Exception:
                        pass

            context.route("**/*", route_handler)
            if not hasattr(context, "route_web_socket"):
                context.close()
                raise BrowserPolicyError(
                    "This Playwright version cannot enforce the WebSocket block; upgrade Playwright."
                )
            context.route_web_socket(
                "**/*",
                lambda web_socket: web_socket.close(
                    code=1008,
                    reason="WebSockets are disabled by the OmniAgent browser policy.",
                ),
            )
            page = context.new_page()
            now = time.monotonic()
            session_id = uuid.uuid4().hex
            self._sessions[session_id] = _BrowserSession(
                context=context,
                page=page,
                created_at=now,
                last_used=now,
                proxy_url=proxy_url,
            )
            return {"session_id": session_id, "privacy_proxy": proxied}

    def _validate_url(self, url: str, proxied: bool) -> str:
        if not isinstance(url, str) or not url or len(url) > 4096:
            raise BrowserPolicyError("URL is empty or exceeds the 4096 character limit.")
        if any(ord(char) < 32 or char.isspace() for char in url):
            raise BrowserPolicyError("URL contains whitespace or control characters.")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise BrowserPolicyError("URL is malformed.") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise BrowserPolicyError("Only absolute HTTP and HTTPS URLs are allowed.")
        if parsed.username is not None or parsed.password is not None:
            raise BrowserPolicyError("URLs containing embedded credentials are blocked.")

        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or any(host.endswith(suffix) for suffix in self._BLOCKED_SUFFIXES):
            raise BrowserPolicyError("Local and special-use hostnames are blocked by this browser policy.")
        if host.endswith(".onion") and not proxied:
            raise BrowserPolicyError("Onion hostnames require an approved privacy proxy.")
        effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
        if effective_port not in self.policy.allowed_ports:
            raise BrowserPolicyError(f"Port {effective_port} is not allowed.")
        if self.policy.allowed_domains and not any(
            host == domain or host.endswith("." + domain)
            for domain in self.policy.allowed_domains
        ):
            raise BrowserPolicyError("The hostname is outside the configured browser allowlist.")

        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if not address.is_global:
                raise BrowserPolicyError("Private, loopback, and reserved IP destinations are blocked.")
        elif not proxied:
            try:
                resolved = socket.getaddrinfo(host, effective_port, type=socket.SOCK_STREAM)
            except OSError as exc:
                raise BrowserPolicyError("The destination hostname could not be resolved.") from exc
            addresses = {entry[4][0] for entry in resolved}
            if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
                raise BrowserPolicyError("The destination resolves to a private or reserved IP address.")
        return host

    def navigate(self, session_id: str, url: str) -> Dict[str, Any]:
        with self._lock:
            proxy_url, proxied = self._egress()
            self._validate_url(url, proxied=proxied)
            session = self._get_session(session_id, expected_proxy_url=proxy_url)
            response = session.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.policy.navigation_timeout_ms,
            )
            session.last_used = time.monotonic()
            return {
                "url": session.page.url,
                "title": session.page.title(),
                "status": response.status if response else None,
                "privacy_proxy": proxied,
            }

    def read_page(self, session_id: str, max_chars: int | None = None) -> Dict[str, Any]:
        with self._lock:
            proxy_url, _ = self._egress()
            session = self._get_session(session_id, expected_proxy_url=proxy_url)
            limit = min(max_chars or self.policy.max_read_chars, self.policy.max_read_chars)
            text = session.page.locator("body").inner_text(timeout=5000)
            session.last_used = time.monotonic()
            return {
                "url": session.page.url,
                "title": session.page.title(),
                "text": text[:limit],
                "truncated": len(text) > limit,
            }

    def fill(self, session_id: str, selector: str, value: str) -> Dict[str, Any]:
        with self._lock:
            proxy_url, _ = self._egress()
            session = self._get_session(session_id, expected_proxy_url=proxy_url)
            self._validate_selector(selector)
            if not isinstance(value, str) or len(value) > 10000:
                raise BrowserPolicyError("Form values must be text no longer than 10000 characters.")
            session.page.locator(selector).fill(value, timeout=5000)
            session.last_used = time.monotonic()
            return {
                "url": session.page.url,
                "selector": selector,
                "filled": True,
                "submitted": False,
            }

    def click(self, session_id: str, selector: str) -> Dict[str, Any]:
        with self._lock:
            proxy_url, _ = self._egress()
            session = self._get_session(session_id, expected_proxy_url=proxy_url)
            self._validate_selector(selector)
            if self.approval_callback is None and not self.policy.allow_unapproved_actions and not (
                self.capability_grant and self.capability_grant.allows("browser.write")
            ):
                raise BrowserPolicyError(
                    "Clicking is disabled until the host provides a human-approval callback."
                )
            request = {
                "action": "browser.click",
                "url": session.page.url,
                "selector": selector,
            }
            if self.approval_callback is not None and not self.approval_callback(request):
                raise BrowserPolicyError("The click was not approved.")
            session.page.locator(selector).click(timeout=5000)
            session.last_used = time.monotonic()
            return {"url": session.page.url, "clicked": selector, "approved": True}

    def close_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False
            session.context.close()
            return True

    def close(self) -> None:
        with self._lock:
            for session in list(self._sessions.values()):
                try:
                    session.context.close()
                except Exception:
                    pass
            self._sessions.clear()
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    def _get_session(self, session_id: str, expected_proxy_url: Optional[str]) -> _BrowserSession:
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise BrowserPolicyError("A valid browser session_id is required.")
        self._cleanup_expired()
        session = self._sessions.get(session_id)
        if session is None:
            raise BrowserPolicyError("Browser session is missing or has expired.")
        if session.proxy_url != expected_proxy_url:
            self._sessions.pop(session_id, None)
            try:
                session.context.close()
            finally:
                raise BrowserPolicyError(
                    "Network privacy settings changed; the previous browser session was closed."
                )
        session.last_used = time.monotonic()
        return session

    @staticmethod
    def _validate_selector(selector: str) -> None:
        if not isinstance(selector, str) or not selector.strip() or len(selector) > 1024:
            raise BrowserPolicyError("Selector must be non-empty text no longer than 1024 characters.")


class BrowserOperateTool(BaseTool):
    def __init__(self, sessions: BrowserSessionManager) -> None:
        self.sessions = sessions

    @property
    def name(self) -> str:
        return "browser.operate"

    @property
    def description(self) -> str:
        return (
            "Create an ephemeral browser session, navigate public websites, read page text, "
            "fill form fields, and request approved clicks. Network egress is controlled by host policy."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create_session", "navigate", "read", "fill", "click", "close_session"],
                },
                "session_id": {"type": "string", "maxLength": 64},
                "url": {"type": "string", "maxLength": 4096},
                "selector": {"type": "string", "maxLength": 1024},
                "value": {"type": "string", "maxLength": 10000},
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 100000},
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action")
        try:
            if action == "create_session":
                output = self.sessions.create_session()
            elif action == "navigate":
                output = self.sessions.navigate(kwargs.get("session_id", ""), kwargs.get("url", ""))
            elif action == "read":
                output = self.sessions.read_page(
                    kwargs.get("session_id", ""),
                    max_chars=kwargs.get("max_chars"),
                )
            elif action == "fill":
                output = self.sessions.fill(
                    kwargs.get("session_id", ""),
                    kwargs.get("selector", ""),
                    kwargs.get("value", ""),
                )
            elif action == "click":
                output = self.sessions.click(
                    kwargs.get("session_id", ""),
                    kwargs.get("selector", ""),
                )
            elif action == "close_session":
                output = {"closed": self.sessions.close_session(kwargs.get("session_id", ""))}
            else:
                return ToolResult(success=False, output=None, error="Unsupported browser action.")
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        return ToolResult(success=True, output=output)


class BrowserOperateSkill(BaseSkill):
    def __init__(
        self,
        network_security_context: Any = None,
        policy: BrowserPolicy | None = None,
        approval_callback: Optional[Callable[[Dict[str, str]], bool]] = None,
        session_manager: BrowserSessionManager | None = None,
        capability_grant: Optional[HostCapabilityGrant] = None,
    ) -> None:
        self.sessions = session_manager or BrowserSessionManager(
            network_security_context=network_security_context,
            policy=policy,
            approval_callback=approval_callback,
            capability_grant=capability_grant,
        )

    @property
    def skill_id(self) -> str:
        return "browser"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Operate ephemeral Playwright sessions under explicit egress and human-approval policies."

    def get_tools(self) -> List[BaseTool]:
        return [BrowserOperateTool(self.sessions)]
