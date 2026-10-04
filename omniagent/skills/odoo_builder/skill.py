"""Bounded Odoo JSON-2 connector and safe website/CRM helpers.

The connector deliberately exposes a short allowlist of Odoo methods. It does
not accept arbitrary model names or method names from an LLM tool call.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import uuid
from typing import Any, Callable, Dict, List, Optional, Protocol
from urllib.parse import urlsplit

import requests

from omniagent.core.models import ToolResult
from omniagent.core.router import BaseTool
from omniagent.skills.base import BaseSkill
from omniagent.security.capabilities import HostCapabilityGrant


class OdooConnectorError(RuntimeError):
    """An Odoo request was rejected or could not be completed safely."""


class OdooApiClient(Protocol):
    def list_pages(self, limit: int = 20) -> List[Dict[str, Any]]: ...
    def search_leads(self, query: str, limit: int = 20) -> List[Dict[str, Any]]: ...
    def create_lead(self, values: Dict[str, str]) -> Any: ...
    def create_page(self, title: str, url: str, sections: List[Dict[str, str]], summary: str, published: bool) -> Any: ...
    def create_product(self, name: str, price: float, description: str, published: bool) -> Any: ...


class OdooJson2Client:
    """Small HTTPS-only Odoo 19 JSON-2 client with opt-in egress.

    Direct egress is off by default. Privacy-proxy egress is allowed only when
    the host explicitly marks the M2 proxy ready. DNS checks are defense in
    depth; deployments still need an outbound firewall to prevent DNS rebinding.
    """

    _ALLOWED_CALLS = {
        ("website.page", "search_read"),
        ("crm.lead", "search_read"),
        ("crm.lead", "create"),
        ("website.page", "create"),
        ("product.template", "create"),
    }

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        database: Optional[str] = None,
        network_security_context: Any = None,
        allow_direct_egress: bool = False,
        proxy_ready: bool = False,
        session: Any = None,
        timeout: float = 12.0,
    ) -> None:
        if not isinstance(base_url, str) or len(base_url) > 2048:
            raise ValueError("Odoo base_url must be a valid HTTPS URL.")
        parsed = urlsplit(base_url.strip())
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Odoo base_url must contain only an HTTPS origin, without credentials or path.")
        if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096:
            raise ValueError("An Odoo API key is required.")
        if database is not None and (not database.strip() or len(database) > 128):
            raise ValueError("database must be non-empty text up to 128 characters.")
        if not 0.2 <= timeout <= 60:
            raise ValueError("timeout must be between 0.2 and 60 seconds.")

        self.base_url = f"https://{parsed.netloc.lower()}"
        self.hostname = parsed.hostname.lower().rstrip(".")
        self._api_key = api_key.strip()
        self.database = database.strip() if database else None
        self.network_security_context = network_security_context
        self.allow_direct_egress = bool(allow_direct_egress)
        self.proxy_ready = bool(proxy_ready)
        self.timeout = timeout
        self.session = session or requests.Session()
        # Avoid implicit proxy configuration, .netrc credentials and ambient env.
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False

    def _configure_egress(self) -> None:
        context = self.network_security_context
        privacy_enabled = bool(getattr(context, "enabled", False))
        if privacy_enabled:
            if not self.proxy_ready:
                raise OdooConnectorError(
                    "Privacy routing is enabled but M2 has not passed its readiness gate; request blocked."
                )
            proxy_url = getattr(context, "proxy_url", None)
            if not proxy_url:
                raise OdooConnectorError("Privacy routing is enabled but no proxy URL is configured.")
            proxy = urlsplit(str(proxy_url))
            if proxy.scheme not in {"socks5", "socks5h", "http", "https"} or not proxy.hostname:
                raise OdooConnectorError("The configured privacy proxy URL is invalid.")
            if proxy.scheme in {"socks5", "socks5h"}:
                try:
                    import socks  # noqa: F401
                except ImportError as exc:
                    raise OdooConnectorError(
                        "SOCKS proxy support is not installed; install the privacy extra."
                    ) from exc
            self.session.proxies.update({"http": str(proxy_url), "https": str(proxy_url)})
            return

        if not self.allow_direct_egress:
            raise OdooConnectorError(
                "Odoo network access is disabled. Configure a ready privacy proxy or explicitly allow direct egress."
            )
        self.session.proxies.clear()
        try:
            answers = socket.getaddrinfo(self.hostname, 443, type=socket.SOCK_STREAM)
            addresses = {entry[4][0] for entry in answers}
        except OSError as exc:
            raise OdooConnectorError("The Odoo hostname could not be resolved.") from exc
        if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
            raise OdooConnectorError("Private, loopback, or reserved Odoo destinations are blocked.")

    def _call(self, model: str, method: str, payload: Dict[str, Any]) -> Any:
        if (model, method) not in self._ALLOWED_CALLS:
            raise OdooConnectorError("This Odoo model/method pair is not enabled by the connector.")
        if not isinstance(payload, dict) or len(json.dumps(payload).encode("utf-8")) > 64_000:
            raise OdooConnectorError("Odoo request payload is invalid or too large.")
        self._configure_egress()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "OmniAgent/0.1",
        }
        if self.database:
            headers["X-Odoo-Database"] = self.database
        url = f"{self.base_url}/json/2/{model}/{method}"
        try:
            response = self.session.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
                allow_redirects=False,
                verify=True,
            )
        except requests.RequestException as exc:
            # Never include request headers or exception repr, which may contain secrets.
            raise OdooConnectorError(f"Odoo request failed ({type(exc).__name__}).") from None
        if 300 <= response.status_code < 400:
            raise OdooConnectorError("Odoo returned a redirect; it was blocked to protect credentials.")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "unspecified")
            raise OdooConnectorError(f"Odoo rate limit reached (Retry-After: {retry_after}).")
        if not 200 <= response.status_code < 300:
            raise OdooConnectorError(f"Odoo request failed with HTTP {response.status_code}.")
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > 2_000_000:
            raise OdooConnectorError("Odoo response exceeded the 2 MB response limit.")
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError):
            raise OdooConnectorError("Odoo returned a non-JSON response.") from None

    def list_pages(self, limit: int = 20) -> List[Dict[str, Any]]:
        limit = _bounded_limit(limit)
        result = self._call(
            "website.page",
            "search_read",
            {"domain": [], "fields": ["id", "name", "url", "website_published"], "limit": limit},
        )
        return _records(result)

    def search_leads(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = _bounded_text(query, "query", 160)
        if not query.strip():
            raise ValueError("query must not be blank.")
        limit = _bounded_limit(limit)
        domain = [
            "|", "|",
            ["name", "ilike", query],
            ["email_from", "ilike", query],
            ["phone", "ilike", query],
        ]
        result = self._call(
            "crm.lead",
            "search_read",
            {"domain": domain, "fields": ["id", "name", "email_from", "phone"], "limit": limit},
        )
        return _records(result)

    def create_lead(self, values: Dict[str, str]) -> Any:
        allowed = {"name", "contact_name", "email_from", "phone", "description"}
        if not isinstance(values, dict) or not values or set(values) - allowed:
            raise ValueError("Lead fields are invalid.")
        vals = {key: _bounded_text(value, key, 4000 if key == "description" else 320)
                for key, value in values.items()}
        if not vals.get("name", "").strip():
            raise ValueError("A lead name is required.")
        result = self._call("crm.lead", "create", {"vals_list": [vals]})
        return _created_id(result)

    def create_page(
        self,
        title: str,
        url: str,
        sections: List[Dict[str, str]],
        summary: str = "",
        published: bool = False,
    ) -> Any:
        title = _bounded_text(title, "title", 160).strip()
        url = _bounded_text(url, "url", 256).strip()
        summary = _bounded_text(summary, "summary", 1000).strip()
        if not title or not re.fullmatch(r"/[A-Za-z0-9/_-]{1,240}", url) or "//" in url:
            raise ValueError("title or relative website URL is invalid.")
        page_html = _build_safe_page_html(title, summary, sections)
        key = f"omniagent.generated_{re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')[:60]}_{uuid.uuid4().hex[:10]}"
        # Odoo 19 docs define website.page records using key, url, type, arch,
        # and is_published. Tenant-specific custom fields are intentionally omitted.
        result = self._call("website.page", "create", {"vals_list": [{
            "name": title,
            "is_published": bool(published),
            "key": key,
            "url": url,
            "type": "qweb",
            "arch": (
                f'<t t-name="{key}"><t t-call="website.layout">'
                f'<div id="wrap" class="oe_structure">{page_html}</div>'
                "</t></t>"
            ),
        }]})
        return _created_id(result)

    def create_product(self, name: str, price: float, description: str = "", published: bool = False) -> Any:
        name = _bounded_text(name, "name", 256).strip()
        description = _bounded_text(description, "description", 4000)
        if not name or isinstance(price, bool) or not isinstance(price, (int, float)) or not 0 <= price <= 1_000_000_000:
            raise ValueError("product name or price is invalid.")
        vals: Dict[str, Any] = {"name": name, "list_price": float(price), "is_published": bool(published)}
        if description:
            vals["description_sale"] = description
        result = self._call("product.template", "create", {"vals_list": [vals]})
        return _created_id(result)


def _created_id(result: Any) -> Any:
    if isinstance(result, (str, int)) and not isinstance(result, bool):
        return result
    if isinstance(result, list) and len(result) == 1:
        return result[0]
    raise OdooConnectorError("Odoo returned an unexpected create result.")


def _bounded_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name} must be text no longer than {maximum} characters.")
    return value


def _bounded_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("limit must be an integer between 1 and 50.")
    return value


def _records(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise OdooConnectorError("Odoo returned an unexpected records payload.")
    return value


class WebsiteDraftTool(BaseTool):
    @property
    def name(self) -> str:
        return "odoo.website_page_draft"

    @property
    def description(self) -> str:
        return "Create a sanitized, unpublished Odoo website page draft for review."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                "summary": {"type": "string", "maxLength": 1000},
                "sections": {
                    "type": "array", "minItems": 1, "maxItems": 12,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "heading": {"type": "string", "minLength": 1, "maxLength": 160},
                            "body": {"type": "string", "minLength": 1, "maxLength": 4000},
                        }, "required": ["heading", "body"],
                    },
                },
            },
            "required": ["title", "sections"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            title = kwargs["title"].strip()
            summary = kwargs.get("summary", "").strip()
            sections = kwargs["sections"]
            if not title or len(sections) > 12:
                raise ValueError("A title and 1-12 page sections are required.")
            return ToolResult(success=True, output={
                "title": title,
                "meta_description": summary[:320],
                "html": _build_safe_page_html(title, summary, sections),
                "published": False,
                "note": "Draft only. Review and import it through the Odoo Website editor.",
            })
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))


class OdooActionTool(BaseTool):
    def __init__(
        self,
        action: str,
        client: Optional[OdooApiClient],
        approval_callback: Optional[Callable[[Dict[str, Any]], bool]],
        capability_grant: Optional[HostCapabilityGrant],
    ) -> None:
        self.action = action
        self.client = client
        self.approval_callback = approval_callback
        self.capability_grant = capability_grant

    @property
    def name(self) -> str:
        return {
            "list_pages": "odoo.website_list_pages",
            "search_leads": "odoo.crm_search_leads",
            "create_lead": "odoo.crm_create_lead",
            "create_page": "odoo.website_create_page",
            "create_product": "odoo.product_create",
        }[self.action]

    @property
    def description(self) -> str:
        return {
            "list_pages": "List existing Odoo website pages; this action is read-only.",
            "search_leads": "Search Odoo CRM leads by name, email, or phone; this action is read-only.",
            "create_lead": "Create one CRM lead under host-granted or callback-approved authority.",
            "create_page": "Create an Odoo website page with sanitized content; host authority controls publication.",
            "create_product": "Create an Odoo product template and optionally publish it under host authority.",
        }[self.action]

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        if self.action == "list_pages":
            properties = {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}
            required: List[str] = []
        elif self.action == "search_leads":
            properties = {
                "query": {"type": "string", "minLength": 1, "maxLength": 160},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }
            required = ["query"]
        elif self.action == "create_lead":
            properties = {
                "name": {"type": "string", "minLength": 1, "maxLength": 320},
                "contact_name": {"type": "string", "maxLength": 320},
                "email_from": {"type": "string", "maxLength": 320},
                "phone": {"type": "string", "maxLength": 320},
                "description": {"type": "string", "maxLength": 4000},
            }
            required = ["name"]
        elif self.action == "create_page":
            properties = {
                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                "url": {"type": "string", "pattern": "^/[A-Za-z0-9/_-]{1,240}$", "maxLength": 256},
                "summary": {"type": "string", "maxLength": 1000},
                "sections": WebsiteDraftTool().parameters_schema["properties"]["sections"],
                "published": {"type": "boolean"},
            }
            required = ["title", "url", "sections"]
        else:
            properties = {
                "name": {"type": "string", "minLength": 1, "maxLength": 256},
                "price": {"type": "number", "minimum": 0, "maximum": 1000000000},
                "description": {"type": "string", "maxLength": 4000},
                "published": {"type": "boolean"},
            }
            required = ["name", "price"]
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    def execute(self, **kwargs: Any) -> ToolResult:
        if self.client is None:
            return ToolResult(success=False, output=None, error="No Odoo connector is configured for this skill instance.")
        try:
            if self.action == "list_pages":
                output = self.client.list_pages(kwargs.get("limit", 20))
            elif self.action == "search_leads":
                output = self.client.search_leads(kwargs["query"], kwargs.get("limit", 20))
            elif self.action == "create_lead":
                values = {key: value for key, value in kwargs.items() if value is not None}
                can_act = bool(self.capability_grant and self.capability_grant.allows("odoo.crm.write"))
                if not can_act and self.approval_callback is None:
                    raise OdooConnectorError("CRM writes require the host to grant odoo.crm.write or provide an approval callback.")
                if not can_act and not self.approval_callback({"action": self.name, "values": values}):
                    raise OdooConnectorError("CRM lead creation was not approved.")
                output = {"id": self.client.create_lead(values), "created": True}
            elif self.action == "create_page":
                published = bool(kwargs.get("published", False))
                can_act = bool(self.capability_grant and self.capability_grant.allows("odoo.website.write"))
                values = {key: kwargs.get(key) for key in ("title", "url", "summary", "sections", "published")}
                if not can_act and self.approval_callback is None:
                    raise OdooConnectorError("Website page creation requires odoo.website.write or a host approval callback.")
                if not can_act and not self.approval_callback({"action": self.name, "values": values}):
                    raise OdooConnectorError("Website page creation was not approved.")
                output = {"id": self.client.create_page(
                    kwargs["title"], kwargs["url"], kwargs["sections"], kwargs.get("summary", ""), published
                ), "created": True, "published": published}
            else:
                published = bool(kwargs.get("published", False))
                can_act = bool(self.capability_grant and self.capability_grant.allows("odoo.product.write"))
                values = {key: kwargs.get(key) for key in ("name", "price", "description", "published")}
                if not can_act and self.approval_callback is None:
                    raise OdooConnectorError("Product creation requires odoo.product.write or a host approval callback.")
                if not can_act and not self.approval_callback({"action": self.name, "values": values}):
                    raise OdooConnectorError("Product creation was not approved.")
                output = {"id": self.client.create_product(
                    kwargs["name"], kwargs["price"], kwargs.get("description", ""), published
                ), "created": True, "published": published}
            return ToolResult(success=True, output=output)
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))


class OdooBuilderSkill(BaseSkill):
    def __init__(
        self,
        client: Optional[OdooApiClient] = None,
        approval_callback: Optional[Callable[[Dict[str, Any]], bool]] = None,
        capability_grant: Optional[HostCapabilityGrant] = None,
    ) -> None:
        self.client = client
        self.approval_callback = approval_callback
        self.capability_grant = capability_grant

    @property
    def skill_id(self) -> str:
        return "odoo"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Build Odoo pages and products, sync CRM leads, and publish changes under host-granted authority."

    def get_tools(self) -> List[BaseTool]:
        return [
            WebsiteDraftTool(),
            OdooActionTool("list_pages", self.client, self.approval_callback, self.capability_grant),
            OdooActionTool("search_leads", self.client, self.approval_callback, self.capability_grant),
            OdooActionTool("create_lead", self.client, self.approval_callback, self.capability_grant),
            OdooActionTool("create_page", self.client, self.approval_callback, self.capability_grant),
            OdooActionTool("create_product", self.client, self.approval_callback, self.capability_grant),
        ]


def _build_safe_page_html(title: str, summary: str, sections: List[Dict[str, str]]) -> str:
    if not isinstance(sections, list) or not 1 <= len(sections) <= 12:
        raise ValueError("1-12 page sections are required.")
    parts = [f"<main><h1>{html.escape(title)}</h1>"]
    if summary:
        parts.append(f"<p>{html.escape(summary)}</p>")
    for section in sections:
        if not isinstance(section, dict) or set(section) - {"heading", "body"}:
            raise ValueError("Each page section must contain only heading and body.")
        heading = _bounded_text(section.get("heading"), "heading", 160).strip()
        body = _bounded_text(section.get("body"), "body", 4000).strip()
        parts.append(
            f"<section><h2>{html.escape(heading)}</h2>"
            f"<p>{html.escape(body).replace(chr(10), '<br>')}</p></section>"
        )
    parts.append("</main>")
    return "".join(parts)
