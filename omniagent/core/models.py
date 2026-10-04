"""
Canonical data models for the OmniAgent framework.
Provides normalized dataclasses for messages, tool calls, tool definitions,
token metrics, and LLM responses across all supported providers.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Union


class MessageRole(str, Enum):
    """Canonical message roles across LLM providers."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

    @classmethod
    def from_string(cls, role_str: str) -> MessageRole:
        """Parse role string case-insensitively, defaulting to USER if unrecognized."""
        normalized = role_str.strip().lower()
        if normalized in ("system", "sys"):
            return cls.SYSTEM
        elif normalized in ("user", "human"):
            return cls.USER
        elif normalized in ("assistant", "ai", "model", "bot"):
            return cls.ASSISTANT
        elif normalized in ("tool", "function"):
            return cls.TOOL
        return cls.USER


@dataclass
class ToolCall:
    """Canonical representation of an LLM tool/function invocation."""
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Auto-deserialize arguments if passed as a JSON string
        if isinstance(self.arguments, str):
            try:
                self.arguments = json.loads(self.arguments)
            except Exception:
                self.arguments = {"_raw": self.arguments}
        elif not isinstance(self.arguments, dict):
            self.arguments = {"value": self.arguments}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolCall:
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            arguments=data.get("arguments") or {},
        )


@dataclass
class ToolDefinition:
    """Canonical JSON Schema definition for a tool available to the LLM."""
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolDefinition:
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            parameters=data.get("parameters") or {},
        )


@dataclass
class TokenUsage:
    """Canonical token accounting across completions."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self):
        if self.total_tokens == 0 and (self.prompt_tokens or self.completion_tokens):
            self.total_tokens = self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TokenUsage:
        return cls(
            prompt_tokens=int(data.get("prompt_tokens") or 0),
            completion_tokens=int(data.get("completion_tokens") or 0),
            total_tokens=int(data.get("total_tokens") or 0),
        )


@dataclass
class LLMResponse:
    """Canonical output returned from any LLM provider."""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop", "tool_calls", "length", "error"
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    provider: str = ""
    raw: Optional[Dict[str, Any]] = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": self.usage.to_dict(),
            "model": self.model,
            "provider": self.provider,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LLMResponse:
        raw_tc = data.get("tool_calls") or []
        tool_calls = [
            ToolCall.from_dict(tc) if isinstance(tc, dict) else tc
            for tc in raw_tc
        ]
        usage = data.get("usage")
        if isinstance(usage, dict):
            usage_obj = TokenUsage.from_dict(usage)
        elif isinstance(usage, TokenUsage):
            usage_obj = usage
        else:
            usage_obj = TokenUsage()

        return cls(
            content=str(data.get("content", "")),
            tool_calls=tool_calls,
            finish_reason=str(data.get("finish_reason", "stop")),
            usage=usage_obj,
            model=str(data.get("model", "")),
            provider=str(data.get("provider", "")),
            raw=data.get("raw"),
        )


@dataclass
class Message:
    """Canonical conversation message."""
    role: MessageRole
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def __post_init__(self):
        if isinstance(self.role, str) and not isinstance(self.role, MessageRole):
            self.role = MessageRole.from_string(self.role)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "role": self.role.value if isinstance(self.role, MessageRole) else str(self.role),
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.tool_calls is not None:
            out["tool_calls"] = [
                tc.to_dict() if isinstance(tc, ToolCall) else tc for tc in self.tool_calls
            ]
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            out["name"] = self.name
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Message:
        tool_calls_data = data.get("tool_calls")
        tool_calls = None
        if tool_calls_data is not None:
            tool_calls = [
                ToolCall.from_dict(tc) if isinstance(tc, dict) else tc
                for tc in tool_calls_data
            ]
        return cls(
            role=MessageRole.from_string(data.get("role", "user")),
            content=str(data.get("content", "")),
            tool_calls=tool_calls,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            timestamp=data.get(
                "timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat()
            ),
        )


@dataclass
class ToolResult:
    """Canonical outcome of a tool execution."""
    success: bool
    output: Any
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolResult:
        return cls(
            success=bool(data.get("success", False)),
            output=data.get("output"),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class NetworkSecurityContext:
    """Security and privacy tunneling context for network requests (M1 <-> M2 contract)."""
    enabled: bool = False
    proxy_url: Optional[str] = None
    route_dns_remotely: bool = True
    scrub_fingerprints: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "proxy_url": self.proxy_url,
            "route_dns_remotely": self.route_dns_remotely,
            "scrub_fingerprints": self.scrub_fingerprints,
        }
