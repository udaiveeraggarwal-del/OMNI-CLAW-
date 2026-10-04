"""
Google Gemini LLM Provider and MockGeminiProvider implementation.
Handles serialization to/from Google Gemini :generateContent wire format.
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


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider translating to :generateContent format."""

    DEFAULT_MODEL = "gemini-1.5-pro"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

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
        return "gemini"

    def format_tools(self, tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
        """Format tools to Gemini functionDeclarations format."""
        func_declarations = []
        for t in tools:
            func_declarations.append({
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            })
        return [{"functionDeclarations": func_declarations}]

    def normalize_request(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Convert canonical inputs to Gemini :generateContent payload."""
        contents: List[Dict[str, Any]] = []
        system_text: List[str] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_text.append(msg.content)
            elif msg.role == MessageRole.TOOL:
                contents.append({
                    "role": "function",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": msg.name or "tool_function",
                                "response": {
                                    "output": msg.content,
                                },
                            }
                        }
                    ],
                })
            elif msg.role == MessageRole.ASSISTANT:
                parts: List[Dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append({
                            "functionCall": {
                                "name": tc.name,
                                "args": tc.arguments,
                            }
                        })
                contents.append({
                    "role": "model",
                    "parts": parts if parts else [{"text": ""}],
                })
            else:  # USER
                contents.append({
                    "role": "user",
                    "parts": [{"text": msg.content or ""}],
                })

        generation_config: Dict[str, Any] = {
            "temperature": temperature,
        }
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }

        if system_text:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_text)}]
            }

        if tools:
            payload["tools"] = self.format_tools(tools)

        return payload

    def normalize_response(self, raw_response: Dict[str, Any]) -> LLMResponse:
        """Parse raw Gemini response dict into canonical LLMResponse."""
        candidates = raw_response.get("candidates") or []
        if not candidates:
            return LLMResponse(
                content="",
                finish_reason="stop",
                model=raw_response.get("model") or self.model,
                provider=self.provider_name,
                raw=raw_response,
            )

        candidate = (candidates[0] if candidates else {}) or {}
        if not isinstance(candidate, dict):
            candidate = {}
        content_obj = candidate.get("content") or {}
        if not isinstance(content_obj, dict):
            content_obj = {}
        parts = content_obj.get("parts") or []
        if not isinstance(parts, list):
            parts = []

        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []

        for part in parts:
            if not isinstance(part, dict):
                continue
            if "text" in part:
                text_parts.append(part.get("text") or "")
            elif "functionCall" in part:
                fc = part.get("functionCall") or {}
                if not isinstance(fc, dict):
                    fc = {}
                call_id = f"gemini_call_{uuid.uuid4().hex[:8]}"
                call_name = fc.get("name") or ""
                call_args = fc.get("args") or {}
                if isinstance(call_args, str):
                    try:
                        call_args = json.loads(call_args)
                    except Exception:
                        call_args = {"_raw": call_args}
                elif not isinstance(call_args, dict):
                    call_args = {"value": call_args}
                tool_calls.append(ToolCall(id=call_id, name=call_name, arguments=call_args))

        raw_finish = (candidate.get("finishReason") or "STOP").upper()
        if raw_finish == "STOP":
            finish_reason = "tool_calls" if tool_calls else "stop"
        elif raw_finish == "MAX_TOKENS":
            finish_reason = "length"
        else:
            finish_reason = raw_finish.lower()

        usage_metadata = raw_response.get("usageMetadata") or {}
        if not isinstance(usage_metadata, dict):
            usage_metadata = {}
        prompt_tokens = int(usage_metadata.get("promptTokenCount") or 0)
        completion_tokens = int(usage_metadata.get("candidatesTokenCount") or 0)
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(usage_metadata.get("totalTokenCount") or (prompt_tokens + completion_tokens)),
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
        """Call live Google Gemini endpoint."""
        payload = self.normalize_request(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        headers = {
            "Content-Type": "application/json",
        }
        session = self.get_http_session()
        response = session.post(url, json=payload, headers=headers, timeout=kwargs.get("timeout", 60))
        response.raise_for_status()
        raw_json = response.json()
        return self.normalize_response(raw_json)


class MockGeminiProvider(GeminiProvider):
    """Deterministic, offline mock implementation of GeminiProvider."""

    def __init__(
        self,
        api_key: str = "mock-gemini-key",
        model: str = "gemini-1.5-pro",
        **kwargs: Any,
    ):
        super().__init__(api_key=api_key, model=model, **kwargs)
        self.queued_responses: List[Dict[str, Any]] = []
        self.call_history: List[Dict[str, Any]] = []

    def queue_response(self, response_data: Dict[str, Any]) -> None:
        """Queue a specific raw Gemini wire response."""
        self.queued_responses.append(response_data)

    def generate(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Simulate realistic Gemini wire response without network calls."""
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
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": first_tool.name,
                                        "args": sample_args,
                                    }
                                }
                            ],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                        "index": 0,
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": len(messages) * 14,
                    "candidatesTokenCount": 20,
                    "totalTokenCount": len(messages) * 14 + 20,
                },
            }
            return self.normalize_response(raw_wire)

        # 3. Default text response or synthesis
        if has_tool_results:
            tool_contents = [m.content for m in messages if m.role == MessageRole.TOOL]
            synthesis_text = f"Synthesized answer from Gemini provider based on results: {'; '.join(tool_contents)}"
        else:
            user_text = last_msg.content if last_msg else "Hello"
            synthesis_text = f"Mock Gemini response to: '{user_text}'"

        raw_wire = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": synthesis_text,
                            }
                        ],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": len(messages) * 12,
                "candidatesTokenCount": len(synthesis_text.split()) * 2,
                "totalTokenCount": len(messages) * 12 + len(synthesis_text.split()) * 2,
            },
        }
        return self.normalize_response(raw_wire)
