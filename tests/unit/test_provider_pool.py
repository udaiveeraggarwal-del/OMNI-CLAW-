"""Offline tests for quota-aware provider routing and the Antigravity bridge."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

import requests

from omniagent.core.models import LLMResponse, Message
from omniagent.core.providers.antigravity_cli import AntigravityCliProvider
from omniagent.core.providers.base import BaseLLMProvider
from omniagent.core.providers.factory import ProviderFactory
from omniagent.core.providers.pool import ProviderPool, ProviderPoolError, ProviderRoute


class StubProvider(BaseLLMProvider):
    def __init__(self, name, model, outcomes=()):
        super().__init__(api_key="test-secret", model=model)
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    @property
    def provider_name(self):
        return self.name

    def format_tools(self, tools):
        return tools

    def normalize_request(self, messages, tools=None, temperature=0.7, max_tokens=None, **kwargs):
        return {}

    def normalize_response(self, raw_response):
        return LLMResponse()

    def generate(self, messages, tools=None, temperature=0.7, max_tokens=None, **kwargs):
        self.calls += 1
        result = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(result, Exception):
            raise result
        return result or LLMResponse(content=self.name, model=self.model, provider=self.name)


def rate_limited(retry_after="30"):
    response = SimpleNamespace(status_code=429, headers={"Retry-After": retry_after})
    error = requests.HTTPError("private response text must not be surfaced")
    error.response = response
    return error


class TestProviderPool(unittest.TestCase):
    def test_rate_limit_fails_over_and_status_does_not_disclose_key(self):
        first = StubProvider("first", "free-a", [rate_limited()])
        second = StubProvider("second", "free-b")
        pool = ProviderPool([
            ProviderRoute("route-a", first, priority=0),
            ProviderRoute("route-b", second, priority=1),
        ], default_cooldown_seconds=2)

        response = pool.generate([Message(role="user", content="hello")])

        self.assertEqual(response.content, "second")
        self.assertEqual((first.calls, second.calls), (1, 1))
        first_status = pool.status()[0]
        self.assertGreaterEqual(first_status["cooldown_seconds"], 1)
        self.assertEqual(first_status["recent_failure"], "rate/quota limit")
        self.assertNotIn("test-secret", repr(pool.status()))

    def test_paid_routes_are_excluded_by_default(self):
        free = StubProvider("free", "free-model")
        paid = StubProvider("paid", "paid-model")
        pool = ProviderPool([
            ProviderRoute("free", free, priority=0),
            ProviderRoute("paid", paid, priority=1, cost_tier="paid"),
        ])

        self.assertEqual(pool.generate([Message(role="user", content="x")]).provider, "free")
        self.assertEqual((free.calls, paid.calls), (1, 0))
        self.assertFalse(pool.status()[1]["eligible_by_budget"])

    def test_local_request_budget_blocks_calls_when_spent(self):
        provider = StubProvider("limited", "free-model")
        pool = ProviderPool([ProviderRoute("limited", provider, requests_per_minute=1)])
        pool.generate([Message(role="user", content="first")])

        with self.assertRaisesRegex(ProviderPoolError, "local budget"):
            pool.generate([Message(role="user", content="second")])
        self.assertEqual(provider.calls, 1)

    def test_server_error_is_not_retried_by_default(self):
        first = StubProvider("first", "model-a", [requests.ConnectionError("network")])
        second = StubProvider("second", "model-b")
        pool = ProviderPool([
            ProviderRoute("a", first, priority=0),
            ProviderRoute("b", second, priority=1),
        ])
        with self.assertRaisesRegex(ProviderPoolError, "ambiguous"):
            pool.generate([Message(role="user", content="once")])
        self.assertEqual(second.calls, 0)


class TestAntigravityBridge(unittest.TestCase):
    def test_factory_registers_route_and_stream_result_is_normalized(self):
        received = {}

        def runner(command, **kwargs):
            received["command"] = command
            received["input"] = kwargs["input"]
            stream = {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "",
                    "structured_output": {
                        "content": "answer",
                        "tool_calls": [],
                    },
                    "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
                },
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(stream) + "\n", stderr="")

        provider = ProviderFactory.create("antigravity", model="gemini-test", timeout_seconds=10, runner=runner)
        self.assertIsInstance(provider, AntigravityCliProvider)
        response = provider.generate([Message(role="user", content="hello")])

        self.assertEqual(response.content, "answer")
        self.assertEqual(response.usage.prompt_tokens, 11)
        self.assertEqual(response.usage.completion_tokens, 7)
        self.assertIn("--input-format", received["command"])
        self.assertIn("stream-json", received["command"])
        self.assertNotIn("hello", received["command"])
        self.assertEqual(json.loads(received["input"])["event"], "user")

    def test_no_tools_produces_valid_empty_call_schema(self):
        schema = AntigravityCliProvider._output_schema(None)
        tool_call = schema["properties"]["tool_calls"]["items"]["properties"]
        self.assertNotIn("enum", tool_call["name"])
        self.assertEqual(schema["properties"]["tool_calls"]["maxItems"], 0)

    def test_subscription_limit_can_fail_over_to_next_route(self):
        def limited_runner(command, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Antigravity subscription quota reached",
            )

        subscription = ProviderFactory.create(
            "antigravity", model="subscribed", timeout_seconds=10, runner=limited_runner
        )
        fallback = StubProvider("fallback", "free-model")
        pool = ProviderPool([
            ProviderRoute("subscription", subscription, priority=0, cost_tier="prepaid"),
            ProviderRoute("free", fallback, priority=1, cost_tier="free"),
        ], allowed_cost_tiers=frozenset({"prepaid", "free"}), default_cooldown_seconds=2)

        self.assertEqual(pool.generate([Message(role="user", content="hello")]).provider, "fallback")


if __name__ == "__main__":
    unittest.main()
