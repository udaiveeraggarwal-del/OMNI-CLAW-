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
from omniagent.core.providers.antigravity_cli import AntigravityCliProvider
from omniagent.core.providers.pool import ProviderPool, ProviderRoute


class ProviderFactory:
    """Factory creating normalized LLM providers by name or configuration."""

    _LIVE_REGISTRY: Dict[str, Type[BaseLLMProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
        "antigravity": AntigravityCliProvider,
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
        "agy": "antigravity",
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

    @classmethod
    def create_pool(
        cls,
        route_configs: List[Dict[str, Any]],
        *,
        allowed_cost_tiers=frozenset({"free"}),
        max_attempts: int = 4,
        default_cooldown_seconds: int = 60,
        max_cooldown_seconds: int = 900,
        failover_on_server_errors: bool = False,
    ) -> ProviderPool:
        """Build a route pool from host-supplied credentials and route policies.

        A provider route can specify ``provider``, ``model``, ``api_key``,
        ``priority``, ``cost_tier``, ``requests_per_minute``,
        ``requests_per_day``, and an ``options`` mapping. Keys remain inside
        the provider instance and never appear in pool status data.
        """
        routes: List[ProviderRoute] = []
        for index, config in enumerate(route_configs):
            if not isinstance(config, dict) or not config.get("provider"):
                raise ValueError("Each provider route requires a provider name.")
            options = config.get("options") or {}
            if not isinstance(options, dict):
                raise ValueError("Provider route options must be a mapping.")
            provider = cls.create(
                provider_name=str(config["provider"]),
                api_key=str(config.get("api_key", "")),
                model=config.get("model"),
                mock=bool(config.get("mock", False)),
                **options,
            )
            routes.append(ProviderRoute(
                route_id=str(config.get("route_id") or f"{provider.provider_name}-{index + 1}"),
                provider=provider,
                priority=int(config.get("priority", index)),
                cost_tier=str(config.get("cost_tier", "free")),
                enabled=bool(config.get("enabled", True)),
                requests_per_minute=config.get("requests_per_minute"),
                requests_per_day=config.get("requests_per_day"),
            ))
        return ProviderPool(
            routes,
            allowed_cost_tiers=frozenset(allowed_cost_tiers),
            max_attempts=max_attempts,
            default_cooldown_seconds=default_cooldown_seconds,
            max_cooldown_seconds=max_cooldown_seconds,
            failover_on_server_errors=failover_on_server_errors,
        )
