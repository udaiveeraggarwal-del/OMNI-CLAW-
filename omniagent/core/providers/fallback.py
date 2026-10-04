import os
import logging
from typing import Dict, Any, List, Optional
from omniagent.core.models import Message, LLMResponse, ToolDefinition
from omniagent.core.providers.base import BaseLLMProvider
from omniagent.security.manager import NetworkSecurityContext

logger = logging.getLogger(__name__)

class ProviderRateLimitError(Exception):
    """Exception raised when a provider is rate limited."""
    pass

class FallbackProviderChain(BaseLLMProvider):
    """
    A provider chain that attempts multiple LLM providers in order.
    If a provider raises a RateLimitError or an HTTP 429, it falls back to the next provider.
    """
    
    def __init__(
        self,
        providers: List[BaseLLMProvider],
        security_context: Optional[NetworkSecurityContext] = None
    ):
        super().__init__(security_context)
        self.providers = providers
        if not self.providers:
            raise ValueError("FallbackProviderChain requires at least one provider.")

    def set_security_context(self, ctx: NetworkSecurityContext) -> None:
        self.security_context = ctx
        for provider in self.providers:
            provider.set_security_context(ctx)

    def generate(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        **kwargs: Any
    ) -> LLMResponse:
        
        last_error = None
        for i, provider in enumerate(self.providers):
            try:
                # Attempt to generate
                return provider.generate(messages, tools, **kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate limit" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
                    logger.warning(f"Provider {provider.__class__.__name__} hit rate limit. Falling back to next...")
                    last_error = e
                    continue
                else:
                    logger.error(f"Provider {provider.__class__.__name__} failed with {e}. Falling back...")
                    last_error = e
                    continue
                    
        raise ProviderRateLimitError(f"All providers in the fallback chain failed. Last error: {last_error}")

    def stream(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        **kwargs: Any
    ) -> Any:
        raise NotImplementedError("Streaming is currently not supported in FallbackProviderChain")
