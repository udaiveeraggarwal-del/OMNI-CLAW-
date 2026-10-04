"""
Base abstract class for all LLM providers in OmniAgent.
Enforces request/response normalization, tool formatting, and security context wrapping.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional
import requests

from omniagent.core.models import (
    LLMResponse,
    Message,
    ToolDefinition,
)
from omniagent.security.manager import NetworkSecurityContext


class BaseLLMProvider(abc.ABC):
    """Abstract base class that all live and mock LLM providers must implement."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        security_context: Optional[NetworkSecurityContext] = None,
        **kwargs: Any,
    ):
        self.api_key = api_key
        self.model = model
        self.security_context = security_context or NetworkSecurityContext()
        self.extra_config = kwargs

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """The canonical name identifier for this provider (e.g. 'openai', 'anthropic', 'gemini')."""
        pass

    def set_security_context(self, ctx: NetworkSecurityContext) -> None:
        """Configure privacy & onion-routing security context (M1 <-> M2 contract)."""
        self.security_context = ctx

    def get_http_session(self) -> requests.Session:
        """Create or configure a requests Session adhering to the security context."""
        session = requests.Session()
        if self.security_context.enabled and self.security_context.proxy_url:
            session.proxies = {
                "http": self.security_context.proxy_url,
                "https": self.security_context.proxy_url,
            }
        return session

    def validate_api_key(self) -> bool:
        """Check whether the provider has a non-empty API key."""
        return bool(self.api_key and len(self.api_key.strip()) > 0)

    @abc.abstractmethod
    def format_tools(self, tools: List[ToolDefinition]) -> Any:
        """Format canonical ToolDefinition objects into provider wire format."""
        pass

    @abc.abstractmethod
    def normalize_request(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Convert canonical messages and tools into the vendor-specific HTTP payload."""
        pass

    @abc.abstractmethod
    def normalize_response(self, raw_response: Dict[str, Any]) -> LLMResponse:
        """Translate vendor-specific wire response into canonical LLMResponse."""
        pass

    @abc.abstractmethod
    def generate(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Execute a completion request and return a canonical LLMResponse."""
        pass
