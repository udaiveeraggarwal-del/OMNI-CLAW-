"""Provider-neutral, approval-gated social publishing skill.

The built-in skill prepares reviewable post payloads and analyzes metrics. Real
posting and analytics require an explicitly injected platform adapter using
the account owner's OAuth grants; no account is contacted by default.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol

from omniagent.core.models import ToolResult
from omniagent.core.router import BaseTool
from omniagent.skills.base import BaseSkill


class SocialConnectorError(RuntimeError):
    """Social action is unavailable or was rejected by policy."""


class SocialPlatformAdapter(Protocol):
    def publish(self, platform: str, post: Dict[str, Any]) -> Dict[str, Any]: ...
    def schedule(self, platform: str, post: Dict[str, Any], publish_at: str) -> Dict[str, Any]: ...
    def analytics(self, platform: str, post_id: str) -> Dict[str, Any]: ...


_SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/-]+=*|(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s,;]+)"
)


class PreparePostTool(BaseTool):
    @property
    def name(self) -> str:
        return "social.prepare_post"

    @property
    def description(self) -> str:
        return "Package generated text and media references into a draft post for review; it does not publish."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": ["instagram", "youtube"]},
                "title": {"type": "string", "maxLength": 200},
                "body": {"type": "string", "minLength": 1, "maxLength": 10000},
                "hashtags": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 80}},
                "media_url": {"type": "string", "maxLength": 2048},
                "tags": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 80}},
            },
            "required": ["platform", "body"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            platform = kwargs["platform"]
            body = kwargs["body"].strip()
            title = kwargs.get("title", "").strip()
            if not body:
                raise ValueError("body must not be blank.")
            if platform == "youtube" and not title:
                raise ValueError("YouTube drafts require a title.")
            hashtags = _normalize_tags(kwargs.get("hashtags", []))
            tags = _normalize_tags(kwargs.get("tags", []))
            media_url = kwargs.get("media_url")
            if media_url is not None and not _is_safe_media_url(media_url):
                raise ValueError("media_url must be a public HTTPS URL without embedded credentials.")
            post = {
                "platform": platform,
                "title": title or None,
                "body": body,
                "hashtags": hashtags,
                "tags": tags,
                "media_url": media_url,
                "status": "draft",
            }
            return ToolResult(success=True, output=post)
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))


class SocialActionTool(BaseTool):
    def __init__(
        self,
        action: str,
        adapter: Optional[SocialPlatformAdapter],
        approval_callback: Optional[Callable[[Dict[str, Any]], bool]],
    ) -> None:
        self.action = action
        self.adapter = adapter
        self.approval_callback = approval_callback

    @property
    def name(self) -> str:
        return {
            "publish": "social.publish",
            "schedule": "social.schedule",
            "analytics": "social.analytics",
        }[self.action]

    @property
    def description(self) -> str:
        return {
            "publish": "Publish a reviewed draft through an injected provider adapter after host approval.",
            "schedule": "Schedule a reviewed draft through an injected provider adapter after host approval.",
            "analytics": "Read account-owned post analytics through an injected provider adapter.",
        }[self.action]

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        if self.action in {"publish", "schedule"}:
            properties: Dict[str, Any] = {
                "platform": {"type": "string", "enum": ["instagram", "youtube"]},
                "post": {"type": "object", "maxProperties": 12},
            }
            required = ["platform", "post"]
            if self.action == "schedule":
                properties["publish_at"] = {"type": "string", "maxLength": 64}
                required.append("publish_at")
        else:
            properties = {
                "platform": {"type": "string", "enum": ["instagram", "youtube"]},
                "post_id": {"type": "string", "minLength": 1, "maxLength": 256},
            }
            required = ["platform", "post_id"]
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    def execute(self, **kwargs: Any) -> ToolResult:
        if self.adapter is None:
            return ToolResult(
                success=False,
                output=None,
                error="No Instagram/YouTube provider adapter is configured; draft creation remains available.",
            )
        try:
            platform = kwargs["platform"]
            if self.action == "analytics":
                output = self.adapter.analytics(platform, kwargs["post_id"])
                return ToolResult(success=True, output=output)

            post = _validate_draft(platform, kwargs["post"])
            if self.action == "schedule":
                publish_at = _validate_future_time(kwargs["publish_at"])
            if self.approval_callback is None:
                raise SocialConnectorError("Publishing is disabled until the host provides an approval callback.")
            approval = {
                "action": self.name,
                "platform": platform,
                "post": post,
                "publish_at": publish_at if self.action == "schedule" else None,
            }
            if not self.approval_callback(approval):
                raise SocialConnectorError("The social publishing action was not approved.")
            if self.action == "schedule":
                output = self.adapter.schedule(platform, post, publish_at)
            else:
                output = self.adapter.publish(platform, post)
            return ToolResult(success=True, output=output)
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))


class AnalyzeMetricsTool(BaseTool):
    @property
    def name(self) -> str:
        return "social.analyze_metrics"

    @property
    def description(self) -> str:
        return "Calculate basic engagement rates from metrics already supplied by the user or a platform adapter."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        counts = {key: {"type": "integer", "minimum": 0} for key in ("impressions", "reach", "likes", "comments", "shares", "saves", "clicks")}
        return {"type": "object", "properties": counts, "required": ["reach"], "additionalProperties": False}

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            reach = kwargs["reach"]
            if isinstance(reach, bool) or not isinstance(reach, int) or reach < 0:
                raise ValueError("reach must be a non-negative integer.")
            interactions = sum(kwargs.get(key, 0) for key in ("likes", "comments", "shares", "saves"))
            clicks = kwargs.get("clicks", 0)
            denominator = reach or kwargs.get("impressions", 0)
            return ToolResult(success=True, output={
                "interactions": interactions,
                "engagement_rate": round(interactions / denominator, 6) if denominator else None,
                "click_through_rate": round(clicks / denominator, 6) if denominator else None,
                "denominator": "reach" if reach else ("impressions" if denominator else None),
                "note": "Rates are fractions; platform definitions may differ.",
            })
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))


def _normalize_tags(values: Any) -> List[str]:
    if not isinstance(values, list) or len(values) > 50:
        raise ValueError("tags must be a list of at most 50 values.")
    result = []
    for value in values:
        if not isinstance(value, str) or len(value) > 80 or not value.strip():
            raise ValueError("each tag must be non-empty text up to 80 characters.")
        result.append(value.strip().lstrip("#"))
    return result


def _is_safe_media_url(value: Any) -> bool:
    from urllib.parse import urlsplit
    if not isinstance(value, str) or len(value) > 2048:
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _validate_draft(platform: str, post: Any) -> Dict[str, Any]:
    if platform not in {"instagram", "youtube"} or not isinstance(post, dict):
        raise ValueError("platform or post draft is invalid.")
    if post.get("platform") != platform or post.get("status") != "draft":
        raise ValueError("Only a matching, unpublished draft can be sent to the provider adapter.")
    if set(post) - {"platform", "title", "body", "hashtags", "tags", "media_url", "status"}:
        raise ValueError("The post draft contains unsupported fields.")
    body = post.get("body")
    if not isinstance(body, str) or not body.strip() or len(body) > 10000:
        raise ValueError("Post body must be non-empty text up to 10000 characters.")
    if platform == "youtube" and (not isinstance(post.get("title"), str) or not post["title"].strip()):
        raise ValueError("YouTube posts require a title.")
    serialized = str(post)
    if _SECRET_PATTERN.search(serialized):
        raise ValueError("Post content appears to contain a credential or access token.")
    return dict(post)


def _validate_future_time(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("publish_at must be an ISO 8601 timestamp with a timezone.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("publish_at must be an ISO 8601 timestamp with a timezone.") from exc
    if parsed.tzinfo is None:
        raise ValueError("publish_at must include a timezone offset.")
    if parsed <= datetime.now(timezone.utc):
        raise ValueError("publish_at must be in the future.")
    return parsed.isoformat()


class SocialMediaSkill(BaseSkill):
    def __init__(
        self,
        adapter: Optional[SocialPlatformAdapter] = None,
        approval_callback: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> None:
        self.adapter = adapter
        self.approval_callback = approval_callback

    @property
    def skill_id(self) -> str:
        return "social"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Prepare social posts, analyze supplied metrics, and gate real publishing through approved provider adapters."

    def get_tools(self) -> List[BaseTool]:
        return [
            PreparePostTool(),
            SocialActionTool("publish", self.adapter, self.approval_callback),
            SocialActionTool("schedule", self.adapter, self.approval_callback),
            SocialActionTool("analytics", self.adapter, self.approval_callback),
            AnalyzeMetricsTool(),
        ]

