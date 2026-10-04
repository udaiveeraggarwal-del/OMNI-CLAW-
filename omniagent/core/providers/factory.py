"""
ProviderFactory for OmniAgent.
Resolves, instantiates, and registers live and mock LLM providers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from omniagent.core.providers.base import BaseLLMProvider
from omniagent.core.providers.openai_provider import OpenAIProvider, MockOpenAIProvider
from omniagent.core.providers.anthropic_provider import AnthropicProvider, MockAnthropicProvider
from omniagent.core.providers.gemini_provider import GeminiProvider, MockGeminiProvider


class ProviderFactory:
    """Factory creating normalized LLM providers by name or configuration."""

    _LIVE_REGISTRY: Dict[str, Type[BaseLLMProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
    }

    _MOCK_REGISTRY: Dict[str, Type[BaseLLMProvider]] = {
        "openai": MockOpenAIProvider,
        "anthropic": MockAnthropicProvider,
        "gemini": MockGeminiProvider,
    }

    _ALIASES: Dict[str, str] = {
        "gpt": "openai",
        "chatgpt": "openai",
        "claude": "anthropic",
        "google": "gemini",
        "vertex": "gemini",
        "gemini-pro": "gemini",
    }

    @classmethod
    def normalize_provider_name(cls, name: str) -> str:
        """Resolve aliases and casing to canonical provider name."""
        cleaned = name.strip().lower()
        return cls._ALIASES.get(cleaned, cleaned)

    @classmethod
    def register_provider(
        cls,
        name: str,
        live_cls: Type[BaseLLMProvider],
        mock_cls: Optional[Type[BaseLLMProvider]] = None,
    ) -> None:
        """Register a custom provider class."""
        canon = name.strip().lower()
        cls._LIVE_REGISTRY[canon] = live_cls
        if mock_cls:
            cls._MOCK_REGISTRY[canon] = mock_cls
        else:
            cls._MOCK_REGISTRY[canon] = live_cls

    @classmethod
    def list_supported_providers(cls) -> List[str]:
        """Return list of canonical provider identifiers."""
        return sorted(list(cls._LIVE_REGISTRY.keys()))

    @classmethod
    def create(
        cls,
        provider_name: str,
        api_key: str = "",
        model: Optional[str] = None,
        mock: bool = False,
        **kwargs: Any,
    ) -> BaseLLMProvider:
        """
        Instantiate an LLM provider.
        If mock=True or api_key begins with 'mock' or is empty and mock is requested,
        the deterministic mock provider is created.
        """
        canonical_name = cls.normalize_provider_name(provider_name)

        # Decide whether to use mock
        is_mock = mock or api_key.startswith("mock") or (not api_key and kwargs.get("default_to_mock", False))

        registry = cls._MOCK_REGISTRY if is_mock else cls._LIVE_REGISTRY

        if canonical_name not in registry:
            available = ", ".join(cls.list_supported_providers())
            raise ValueError(
                f"Unknown provider '{provider_name}'. Supported providers: {available}"
            )

        provider_cls = registry[canonical_name]
        return provider_cls(api_key=api_key, model=model, **kwargs)

    @classmethod
    def create_mock(
        cls,
        provider_name: str,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> BaseLLMProvider:
        """Convenience helper to create a deterministic mock provider directly."""
        return cls.create(
            provider_name=provider_name,
            api_key=f"mock-{cls.normalize_provider_name(provider_name)}-key",
            model=model,
            mock=True,
            **kwargs,
        )
