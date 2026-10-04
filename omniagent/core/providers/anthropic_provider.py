"""
Anthropic LLM Provider and MockAnthropicProvider implementation.
Handles serialization to/from Anthropic /v1/messages wire format.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import uuid

from omniagent.core.models import (
    LLMResponse,
    Message,
    MessageRole,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from omniagent.core.providers.base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    """Anthropic API provider translating to /v1/messages format."""

    DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
    DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str = "",
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ):
        super().__init__(
            api_key=api_key,
            model=model or self.DEFAULT_MODEL,
            **kwargs,
        )
        self.base_url = (base_url or self.extra_config.get("base_url") or self.DEFAULT_BASE_URL).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def format_tools(self, tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
        """Format tools to Anthropic schema (input_schema)."""
        formatted = []
        for t in tools:
            formatted.append({
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            })
        return formatted

    def normalize_request(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Convert canonical inputs to Anthropic /v1/messages payload."""
        system_prompts: List[str] = []
        wire_messages: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_prompts.append(msg.content)
            elif msg.role == MessageRole.TOOL:
                # In Anthropic, tool execution results are sent under role 'user' with type 'tool_result'
                wire_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id or f"toolu_{uuid.uuid4().hex[:8]}",
                            "content": msg.content or "",
                        }
                    ],
                })
            elif msg.role == MessageRole.ASSISTANT:
                content_blocks: List[Dict[str, Any]] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                wire_messages.append({
                    "role": "assistant",
                    "content": content_blocks if content_blocks else msg.content or "",
                })
            else:  # USER
                wire_messages.append({
                    "role": "user",
                    "content": msg.content or "",
                })

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }

        if system_prompts:
            payload["system"] = "\n\n".join(system_prompts)

        if tools:
            payload["tools"] = self.format_tools(tools)

        return payload

    def normalize_response(self, raw_response: Dict[str, Any]) -> LLMResponse:
        """Parse raw Anthropic response dict into canonical LLMResponse."""
        content_blocks = raw_response.get("content") or []
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []

        if isinstance(content_blocks, str):
            text_parts.append(content_blocks)
        elif isinstance(content_blocks, list):
            for block in content_blocks:
                if isinstance(block, dict):
                    b_type = block.get("type")
                    if b_type == "text":
                        text_parts.append(block.get("text") or "")
                    elif b_type == "tool_use":
                        tc_id = block.get("id") or str(uuid.uuid4())
                        tc_name = block.get("name") or ""
                        tc_input = block.get("input") or {}
                        if isinstance(tc_input, str):
                            try:
                                tc_input = json.loads(tc_input)
                            except Exception:
                                tc_input = {"_raw": tc_input}
                        elif not isinstance(tc_input, dict):
                            tc_input = {"value": tc_input}
                        tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=tc_input))

        stop_reason = raw_response.get("stop_reason") or "end_turn"
        if stop_reason == "end_turn":
            finish_reason = "stop"
        elif stop_reason == "tool_use":
            finish_reason = "tool_calls"
        elif stop_reason == "max_tokens":
            finish_reason = "length"
        else:
            finish_reason = stop_reason

        raw_usage = raw_response.get("usage") or {}
        if not isinstance(raw_usage, dict):
            raw_usage = {}
        prompt_tokens = int(raw_usage.get("input_tokens") or 0)
        completion_tokens = int(raw_usage.get("output_tokens") or 0)
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        return LLMResponse(
            content="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else finish_reason,
            usage=usage,
            model=raw_response.get("model") or self.model,
            provider=self.provider_name,
            raw=raw_response,
        )

    def generate(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Call live Anthropic endpoint."""
        payload = self.normalize_request(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        session = self.get_http_session()
        response = session.post(url, json=payload, headers=headers, timeout=kwargs.get("timeout", 60))
        response.raise_for_status()
        raw_json = response.json()
        return self.normalize_response(raw_json)


class MockAnthropicProvider(AnthropicProvider):
    """Deterministic, offline mock implementation of AnthropicProvider."""

    def __init__(
        self,
        api_key: str = "mock-anthropic-key",
        model: str = "claude-3-5-sonnet-20241022",
        **kwargs: Any,
    ):
        super().__init__(api_key=api_key, model=model, **kwargs)
        self.queued_responses: List[Dict[str, Any]] = []
        self.call_history: List[Dict[str, Any]] = []

    def queue_response(self, response_data: Dict[str, Any]) -> None:
        """Queue a specific raw Anthropic wire response."""
        self.queued_responses.append(response_data)

    def generate(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Simulate realistic Anthropic wire response without network calls."""
        payload = self.normalize_request(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        self.call_history.append(payload)

        # 1. Return queued response if present
        if self.queued_responses:
            raw_response = self.queued_responses.pop(0)
            return self.normalize_response(raw_response)

        # 2. Check if the latest message was a tool result
        last_msg = messages[-1] if messages else None
        has_tool_results = any(m.role == MessageRole.TOOL for m in messages)

        # If tools are available and no tool result yet, emit tool calls if requested
        if tools and not has_tool_results and any("fetch" in m.content.lower() or "tool" in m.content.lower() or "calculate" in m.content.lower() for m in messages if m.role == MessageRole.USER):
            first_tool = tools[0]
            call_id = f"toolu_{uuid.uuid4().hex[:10]}"
            sample_args: Dict[str, Any] = {}
            props = first_tool.parameters.get("properties", {})
            for prop_name, prop_spec in props.items():
                p_type = prop_spec.get("type", "string")
                if p_type == "string":
                    sample_args[prop_name] = "test-query"
                elif p_type in ("integer", "number"):
                    sample_args[prop_name] = 42
                elif p_type == "array":
                    sample_args[prop_name] = [1, 2, 3]
                elif p_type == "boolean":
                    sample_args[prop_name] = True
                else:
                    sample_args[prop_name] = {}

            raw_wire = {
                "id": f"msg_{uuid.uuid4().hex[:14]}",
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": [
                    {
                        "type": "text",
                        "text": f"Invoking tool {first_tool.name} to fulfill request.",
                    },
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": first_tool.name,
                        "input": sample_args,
                    },
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": len(messages) * 16,
                    "output_tokens": 30,
                },
            }
            return self.normalize_response(raw_wire)

        # 3. Default text response or synthesis
        if has_tool_results:
            tool_contents = [m.content for m in messages if m.role == MessageRole.TOOL]
            synthesis_text = f"Synthesized answer from Anthropic provider based on results: {'; '.join(tool_contents)}"
        else:
            user_text = last_msg.content if last_msg else "Hello"
            synthesis_text = f"Mock Anthropic response to: '{user_text}'"

        raw_wire = {
            "id": f"msg_{uuid.uuid4().hex[:14]}",
            "type": "message",
            "role": "assistant",
            "model": self.model,
            "content": [
                {
                    "type": "text",
                    "text": synthesis_text,
                }
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": len(messages) * 14,
                "output_tokens": len(synthesis_text.split()) * 2,
            },
        }
        return self.normalize_response(raw_wire)
