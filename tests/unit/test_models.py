"""
Unit tests for canonical data models in omniagent.core.models.
"""

import json
import unittest

from omniagent.core.models import (
    LLMResponse,
    Message,
    MessageRole,
    NetworkSecurityContext,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


class TestCanonicalModels(unittest.TestCase):

    def test_message_roles(self):
        self.assertEqual(MessageRole.SYSTEM.value, "system")
        self.assertEqual(MessageRole.USER.value, "user")
        self.assertEqual(MessageRole.ASSISTANT.value, "assistant")
        self.assertEqual(MessageRole.TOOL.value, "tool")

        # Parsing variations
        self.assertEqual(MessageRole.from_string("SYS"), MessageRole.SYSTEM)
        self.assertEqual(MessageRole.from_string("human"), MessageRole.USER)
        self.assertEqual(MessageRole.from_string("model"), MessageRole.ASSISTANT)
        self.assertEqual(MessageRole.from_string("function"), MessageRole.TOOL)
        self.assertEqual(MessageRole.from_string("unknown_role"), MessageRole.USER)

    def test_tool_call_serialization(self):
        # Dict arguments
        tc1 = ToolCall(id="call_1", name="calculator", arguments={"a": 1, "b": 2})
        self.assertEqual(tc1.id, "call_1")
        self.assertEqual(tc1.name, "calculator")
        self.assertEqual(tc1.arguments, {"a": 1, "b": 2})
        tc1_dict = tc1.to_dict()
        self.assertEqual(tc1_dict["name"], "calculator")
        self.assertEqual(ToolCall.from_dict(tc1_dict).arguments, {"a": 1, "b": 2})

        # String json arguments auto-parsed
        tc2 = ToolCall(id="call_2", name="search", arguments='{"query": "omniagent"}')
        self.assertEqual(tc2.arguments, {"query": "omniagent"})

    def test_tool_definition(self):
        td = ToolDefinition(
            name="weather_tool",
            description="Fetch current weather",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        )
        data = td.to_dict()
        self.assertEqual(data["name"], "weather_tool")
        rebuilt = ToolDefinition.from_dict(data)
        self.assertEqual(rebuilt.description, "Fetch current weather")
        self.assertIn("properties", rebuilt.parameters)

    def test_token_usage_accounting(self):
        usage = TokenUsage(prompt_tokens=150, completion_tokens=50)
        self.assertEqual(usage.total_tokens, 200)

        usage_dict = usage.to_dict()
        self.assertEqual(usage_dict["total_tokens"], 200)
        rebuilt = TokenUsage.from_dict(usage_dict)
        self.assertEqual(rebuilt.prompt_tokens, 150)
        self.assertEqual(rebuilt.completion_tokens, 50)

    def test_llm_response_lifecycle(self):
        tc = ToolCall(id="c1", name="fetch", arguments={"q": "foo"})
        resp = LLMResponse(
            content="Invoking fetch tool",
            tool_calls=[tc],
            finish_reason="tool_calls",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20),
            model="gpt-4o",
            provider="openai",
            raw={"id": "chatcmpl-123"},
        )
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(len(resp.tool_calls), 1)

        d = resp.to_dict()
        rebuilt = LLMResponse.from_dict(d)
        self.assertEqual(rebuilt.content, "Invoking fetch tool")
        self.assertEqual(rebuilt.tool_calls[0].name, "fetch")
        self.assertEqual(rebuilt.usage.total_tokens, 30)
        self.assertEqual(rebuilt.provider, "openai")
        self.assertEqual(rebuilt.model, "gpt-4o")

    def test_message_lifecycle(self):
        msg = Message(
            role="user",
            content="Hello world",
        )
        self.assertEqual(msg.role, MessageRole.USER)
        self.assertIsNotNone(msg.timestamp)

        d = msg.to_dict()
        rebuilt = Message.from_dict(d)
        self.assertEqual(rebuilt.role, MessageRole.USER)
        self.assertEqual(rebuilt.content, "Hello world")

    def test_tool_result_lifecycle(self):
        tr = ToolResult(success=True, output={"status": "ok", "count": 42}, metadata={"duration_ms": 12})
        self.assertTrue(tr.success)
        self.assertIsNone(tr.error)

        d = tr.to_dict()
        rebuilt = ToolResult.from_dict(d)
        self.assertTrue(rebuilt.success)
        self.assertEqual(rebuilt.output["count"], 42)
        self.assertEqual(rebuilt.metadata["duration_ms"], 12)

    def test_network_security_context(self):
        ctx = NetworkSecurityContext(
            enabled=True,
            proxy_url="socks5://127.0.0.1:9055",
            route_dns_remotely=True,
            scrub_fingerprints=True,
        )
        self.assertTrue(ctx.enabled)
        self.assertEqual(ctx.proxy_url, "socks5://127.0.0.1:9055")
        d = ctx.to_dict()
        self.assertEqual(d["proxy_url"], "socks5://127.0.0.1:9055")


if __name__ == "__main__":
    unittest.main()
