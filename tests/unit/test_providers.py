"""
Unit tests for Provider Normalization and ProviderFactory in OmniAgent.
Directly verifies Acceptance Criterion:
- Automated tests pass for initializing the LLM abstraction layer with at least 3 different mock API providers.
- Verifies request and response normalization across OpenAI, Anthropic, and Gemini wire schemas.
"""

import unittest

from omniagent.core.models import (
    LLMResponse,
    Message,
    MessageRole,
    NetworkSecurityContext,
    ToolCall,
    ToolDefinition,
)
from omniagent.core.providers.base import BaseLLMProvider
from omniagent.core.providers.factory import ProviderFactory
from omniagent.core.providers.openai_provider import MockOpenAIProvider
from omniagent.core.providers.anthropic_provider import MockAnthropicProvider
from omniagent.core.providers.gemini_provider import MockGeminiProvider


class TestProvidersAbstraction(unittest.TestCase):

    def setUp(self):
        # Sample canonical messages
        self.messages = [
            Message(role=MessageRole.SYSTEM, content="You are a helpful assistant."),
            Message(role=MessageRole.USER, content="Fetch data for research."),
        ]
        # Sample canonical tool definition
        self.sample_tool = ToolDefinition(
            name="data_fetcher",
            description="Fetches raw data from remote store",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term"},
                },
                "required": ["query"],
            },
        )

    def test_acceptance_three_mock_providers_initialization(self):
        """
        Acceptance Criterion Verification:
        Automated tests pass for initializing the LLM abstraction layer with at least 3 different mock API providers.
        """
        p_openai = ProviderFactory.create_mock("openai")
        p_anthropic = ProviderFactory.create_mock("anthropic")
        p_gemini = ProviderFactory.create_mock("gemini")

        # Verify instance types
        self.assertIsInstance(p_openai, MockOpenAIProvider)
        self.assertIsInstance(p_anthropic, MockAnthropicProvider)
        self.assertIsInstance(p_gemini, MockGeminiProvider)

        # Verify base provider compliance
        for p in [p_openai, p_anthropic, p_gemini]:
            self.assertIsInstance(p, BaseLLMProvider)
            self.assertTrue(p.validate_api_key())
            self.assertTrue(len(p.provider_name) > 0)

    def test_factory_aliases_and_registration(self):
        p_gpt = ProviderFactory.create("gpt", mock=True)
        self.assertIsInstance(p_gpt, MockOpenAIProvider)

        p_claude = ProviderFactory.create("claude", mock=True)
        self.assertIsInstance(p_claude, MockAnthropicProvider)

        p_google = ProviderFactory.create("google", mock=True)
        self.assertIsInstance(p_google, MockGeminiProvider)

        with self.assertRaises(ValueError):
            ProviderFactory.create("unknown_unsupported_provider", mock=True)

    def test_openai_request_response_normalization(self):
        provider = ProviderFactory.create_mock("openai")

        # Request normalization
        payload = provider.normalize_request(self.messages, tools=[self.sample_tool])
        self.assertIn("messages", payload)
        self.assertIn("tools", payload)
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["tools"][0]["function"]["name"], "data_fetcher")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")

        # Generate standard text response
        resp = provider.generate([Message(role=MessageRole.USER, content="Hello")])
        self.assertIsInstance(resp, LLMResponse)
        self.assertEqual(resp.provider, "openai")
        self.assertTrue(len(resp.content) > 0)
        self.assertFalse(resp.has_tool_calls)
        self.assertEqual(resp.finish_reason, "stop")
        self.assertGreater(resp.usage.total_tokens, 0)

        # Generate tool invocation response
        tool_resp = provider.generate(
            [Message(role=MessageRole.USER, content="Please fetch numeric data")],
            tools=[self.sample_tool],
        )
        self.assertTrue(tool_resp.has_tool_calls)
        self.assertEqual(tool_resp.finish_reason, "tool_calls")
        self.assertEqual(tool_resp.tool_calls[0].name, "data_fetcher")

    def test_anthropic_request_response_normalization(self):
        provider = ProviderFactory.create_mock("anthropic")

        # Request normalization
        payload = provider.normalize_request(self.messages, tools=[self.sample_tool])
        self.assertIn("system", payload)
        self.assertEqual(payload["system"], "You are a helpful assistant.")
        self.assertIn("tools", payload)
        self.assertEqual(payload["tools"][0]["name"], "data_fetcher")
        self.assertIn("input_schema", payload["tools"][0])

        # Generate standard text response
        resp = provider.generate([Message(role=MessageRole.USER, content="Hello")])
        self.assertIsInstance(resp, LLMResponse)
        self.assertEqual(resp.provider, "anthropic")
        self.assertTrue(len(resp.content) > 0)
        self.assertFalse(resp.has_tool_calls)
        self.assertEqual(resp.finish_reason, "stop")
        self.assertGreater(resp.usage.total_tokens, 0)

        # Generate tool invocation response
        tool_resp = provider.generate(
            [Message(role=MessageRole.USER, content="Please fetch research data")],
            tools=[self.sample_tool],
        )
        self.assertTrue(tool_resp.has_tool_calls)
        self.assertEqual(tool_resp.finish_reason, "tool_calls")
        self.assertEqual(tool_resp.tool_calls[0].name, "data_fetcher")

    def test_gemini_request_response_normalization(self):
        provider = ProviderFactory.create_mock("gemini")

        # Request normalization
        payload = provider.normalize_request(self.messages, tools=[self.sample_tool])
        self.assertIn("systemInstruction", payload)
        self.assertEqual(
            payload["systemInstruction"]["parts"][0]["text"],
            "You are a helpful assistant.",
        )
        self.assertIn("tools", payload)
        self.assertEqual(
            payload["tools"][0]["functionDeclarations"][0]["name"],
            "data_fetcher",
        )

        # Generate standard text response
        resp = provider.generate([Message(role=MessageRole.USER, content="Hello")])
        self.assertIsInstance(resp, LLMResponse)
        self.assertEqual(resp.provider, "gemini")
        self.assertTrue(len(resp.content) > 0)
        self.assertFalse(resp.has_tool_calls)
        self.assertEqual(resp.finish_reason, "stop")
        self.assertGreater(resp.usage.total_tokens, 0)

        # Generate tool invocation response
        tool_resp = provider.generate(
            [Message(role=MessageRole.USER, content="Please fetch data")],
            tools=[self.sample_tool],
        )
        self.assertTrue(tool_resp.has_tool_calls)
        self.assertEqual(tool_resp.finish_reason, "tool_calls")
        self.assertEqual(tool_resp.tool_calls[0].name, "data_fetcher")

    def test_custom_wire_response_queuing(self):
        """Verify providers faithfully parse custom vendor wire schemas."""
        # 1. Queued OpenAI wire format
        p_openai = ProviderFactory.create_mock("openai")
        p_openai.queue_response({
            "id": "chatcmpl-test-custom",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Custom OpenAI text"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        })
        resp_o = p_openai.generate([Message(role=MessageRole.USER, content="Hi")])
        self.assertEqual(resp_o.content, "Custom OpenAI text")
        self.assertEqual(resp_o.usage.total_tokens, 15)

        # 2. Queued Anthropic wire format
        p_anthropic = ProviderFactory.create_mock("anthropic")
        p_anthropic.queue_response({
            "id": "msg_test_custom",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Custom Anthropic text"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 8, "output_tokens": 12},
        })
        resp_a = p_anthropic.generate([Message(role=MessageRole.USER, content="Hi")])
        self.assertEqual(resp_a.content, "Custom Anthropic text")
        self.assertEqual(resp_a.usage.total_tokens, 20)

        # 3. Queued Gemini wire format
        p_gemini = ProviderFactory.create_mock("gemini")
        p_gemini.queue_response({
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Custom Gemini text"}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 9,
                "totalTokenCount": 16,
            },
        })
        resp_g = p_gemini.generate([Message(role=MessageRole.USER, content="Hi")])
        self.assertEqual(resp_g.content, "Custom Gemini text")
        self.assertEqual(resp_g.usage.total_tokens, 16)

    def test_security_context_integration(self):
        provider = ProviderFactory.create_mock("openai")
        sec_ctx = NetworkSecurityContext(
            enabled=True,
            proxy_url="socks5://127.0.0.1:9055",
            route_dns_remotely=True,
        )
        provider.set_security_context(sec_ctx)
        self.assertTrue(provider.security_context.enabled)
        session = provider.get_http_session()
        self.assertEqual(session.proxies.get("http"), "socks5://127.0.0.1:9055")
        self.assertEqual(session.proxies.get("https"), "socks5://127.0.0.1:9055")


if __name__ == "__main__":
    unittest.main()
