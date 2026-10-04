"""
OpenAI LLM Provider and MockOpenAIProvider implementation.
Handles serialization to/from OpenAI /v1/chat/completions wire format.
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


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider translating to /v1/chat/completions format."""

    DEFAULT_MODEL = "gpt-4o"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

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
        return "openai"

    def format_tools(self, tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
        """Format tools to OpenAI function calling schema."""
        formatted = []
        for t in tools:
            formatted.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
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
        """Convert canonical inputs to OpenAI /v1/chat/completions payload."""
        wire_messages: List[Dict[str, Any]] = []

        for msg in messages:
            role_val = msg.role.value if isinstance(msg.role, MessageRole) else str(msg.role)
            m_dict: Dict[str, Any] = {"role": role_val, "content": msg.content or ""}

            if msg.role == MessageRole.TOOL:
                m_dict["role"] = "tool"
                if msg.tool_call_id:
                    m_dict["tool_call_id"] = msg.tool_call_id
                if msg.name:
                    m_dict["name"] = msg.name
            elif msg.role == MessageRole.ASSISTANT and msg.tool_calls:
                m_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else str(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            wire_messages.append(m_dict)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        if tools:
            payload["tools"] = self.format_tools(tools)
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        return payload

    def normalize_response(self, raw_response: Dict[str, Any]) -> LLMResponse:
        """Parse raw OpenAI response dict into canonical LLMResponse."""
        choices = raw_response.get("choices") or []
        if not choices:
            return LLMResponse(
                content="",
                finish_reason="stop",
                model=raw_response.get("model") or self.model,
                provider=self.provider_name,
                raw=raw_response,
            )

        choice = choices[0] or {}
        if not isinstance(choice, dict):
            choice = {}
        msg = choice.get("message") or {}
        if not isinstance(msg, dict):
            msg = {}
        content = msg.get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"

        tool_calls: List[ToolCall] = []
        raw_tool_calls = msg.get("tool_calls") or []
        for rtc in raw_tool_calls:
            if not isinstance(rtc, dict):
                continue
            fn = rtc.get("function") or {}
            if not isinstance(fn, dict):
                fn = {}
            call_id = rtc.get("id") or str(uuid.uuid4())
            call_name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {"_raw": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            tool_calls.append(ToolCall(id=call_id, name=call_name, arguments=args))

        raw_usage = raw_response.get("usage") or {}
        if not isinstance(raw_usage, dict):
            raw_usage = {}
        usage = TokenUsage(
            prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
            completion_tokens=int(raw_usage.get("completion_tokens") or 0),
            total_tokens=int(raw_usage.get("total_tokens") or 0),
        )

        return LLMResponse(
            content=content,
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
        """Call live OpenAI endpoint."""
        payload = self.normalize_request(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        session = self.get_http_session()
        response = session.post(url, json=payload, headers=headers, timeout=kwargs.get("timeout", 60))
        response.raise_for_status()
        raw_json = response.json()
        return self.normalize_response(raw_json)


class MockOpenAIProvider(OpenAIProvider):
    """Deterministic, offline mock implementation of OpenAIProvider."""

    def __init__(
        self,
        api_key: str = "mock-openai-key",
        model: str = "gpt-4o",
        **kwargs: Any,
    ):
        super().__init__(api_key=api_key, model=model, **kwargs)
        self.queued_responses: List[Dict[str, Any]] = []
        self.call_history: List[Dict[str, Any]] = []

    def queue_response(self, response_data: Dict[str, Any]) -> None:
        """Queue a specific raw OpenAI wire response to return on subsequent generate() call."""
        self.queued_responses.append(response_data)

    def generate(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Simulate realistic OpenAI wire response without network calls."""
        payload = self.normalize_request(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        self.call_history.append(payload)

        # 1. Check if user queued a deterministic wire response
        if self.queued_responses:
            raw_response = self.queued_responses.pop(0)
            return self.normalize_response(raw_response)

        # 2. Check if the latest message was a tool result
        last_msg = messages[-1] if messages else None
        has_tool_results = any(m.role == MessageRole.TOOL for m in messages)

        # If tools are available and no tool result yet, emit tool calls if requested
        if tools and not has_tool_results and any("fetch" in m.content.lower() or "tool" in m.content.lower() or "calculate" in m.content.lower() for m in messages if m.role == MessageRole.USER):
            first_tool = tools[0]
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            sample_args: Dict[str, Any] = {}
            # Generate sample arguments conforming to the tool schema
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
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": 1728000000,
                "model": self.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": first_tool.name,
                                        "arguments": json.dumps(sample_args),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": len(messages) * 15,
                    "completion_tokens": 25,
                    "total_tokens": len(messages) * 15 + 25,
                },
            }
            return self.normalize_response(raw_wire)

        # 3. Default text response or synthesis
        if has_tool_results:
            tool_contents = [m.content for m in messages if m.role == MessageRole.TOOL]
            synthesis_text = f"Synthesized answer from OpenAI provider based on results: {'; '.join(tool_contents)}"
        else:
            user_text = last_msg.content if last_msg else "Hello"
            synthesis_text = f"Mock OpenAI response to: '{user_text}'"

        raw_wire = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": 1728000000,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": synthesis_text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(messages) * 12,
                "completion_tokens": len(synthesis_text.split()) * 2,
                "total_tokens": len(messages) * 12 + len(synthesis_text.split()) * 2,
            },
        }
        return self.normalize_response(raw_wire)
