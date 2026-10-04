"""
Hierarchical memory subsystem for OmniAgent.
Includes:
- SlidingWindowMemory: Token/message bounded rolling window with pinned system message.
- SummaryMemory: Auto-compressing conversation buffer using LLM or structured distillation.
- SemanticMemory: Pure-Python keyword and token-overlap episodic relevance store.
"""

from __future__ import annotations

import abc
import datetime
import math
import re
from typing import Any, Dict, List, Optional, Set

from omniagent.core.models import Message, MessageRole
from omniagent.core.providers.base import BaseLLMProvider


class BaseMemory(abc.ABC):
    """Abstract interface for agent conversation memory."""

    @abc.abstractmethod
    def add_message(self, message: Message) -> None:
        """Add a new message to memory."""
        pass

    @abc.abstractmethod
    def get_messages(self) -> List[Message]:
        """Retrieve active messages for context assembly."""
        pass

    @abc.abstractmethod
    def clear(self) -> None:
        """Clear all messages in memory."""
        pass


class SlidingWindowMemory(BaseMemory):
    """
    Sliding window memory retaining the most recent messages.
    Automatically preserves the initial SYSTEM message (if present) to prevent
    instruction drift while discarding oldest conversation turns.
    """

    def __init__(self, max_messages: int = 10, max_tokens: Optional[int] = None):
        if max_messages < 1:
            raise ValueError("max_messages must be at least 1")
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self._messages: List[Message] = []

    def add_message(self, message: Message) -> None:
        self._messages.append(message)

    def _estimate_tokens(self, message: Message) -> int:
        # Heuristic: ~4 characters per token
        char_count = len(message.content or "")
        if message.tool_calls:
            for tc in message.tool_calls:
                char_count += len(tc.name) + len(str(tc.arguments))
        return max(1, math.ceil(char_count / 4))

    def get_messages(self) -> List[Message]:
        if not self._messages:
            return []

        # Check if first message is a pinned SYSTEM prompt
        has_system = self._messages[0].role == MessageRole.SYSTEM
        system_msg = self._messages[0] if has_system else None
        non_system_msgs = self._messages[1:] if has_system else self._messages

        # Window by count
        allowed_count = self.max_messages - (1 if has_system else 0)
        recent_msgs = non_system_msgs[-allowed_count:] if allowed_count > 0 else []

        # Window by tokens if configured
        if self.max_tokens is not None:
            available_tokens = self.max_tokens
            if system_msg:
                available_tokens -= self._estimate_tokens(system_msg)

            token_bounded: List[Message] = []
            for msg in reversed(recent_msgs):
                msg_tokens = self._estimate_tokens(msg)
                if available_tokens - msg_tokens >= 0 or not token_bounded:
                    token_bounded.insert(0, msg)
                    available_tokens -= msg_tokens
                else:
                    break
            recent_msgs = token_bounded

        if system_msg:
            return [system_msg] + recent_msgs
        return recent_msgs

    def clear(self) -> None:
        self._messages.clear()


class SummaryMemory(BaseMemory):
    """
    Rolling summary memory.
    Compresses older conversation history into an accumulating summary
    once unsummarized message threshold is reached.
    """

    def __init__(
        self,
        max_unsummarized_messages: int = 4,
        provider: Optional[BaseLLMProvider] = None,
        summary_prefix: str = "Summary of previous conversation:",
    ):
        self.max_unsummarized_messages = max(2, max_unsummarized_messages)
        self.provider = provider
        self.summary_prefix = summary_prefix
        self.summary: str = ""
        self._all_messages: List[Message] = []
        self._system_message: Optional[Message] = None
        self._unsummarized_messages: List[Message] = []

    def add_message(self, message: Message) -> None:
        self._all_messages.append(message)
        if message.role == MessageRole.SYSTEM and self._system_message is None:
            self._system_message = message
            return

        self._unsummarized_messages.append(message)
        if len(self._unsummarized_messages) > self.max_unsummarized_messages:
            self._compress()

    def _compress(self) -> None:
        # Move the oldest batch of messages into the summary
        num_to_summarize = len(self._unsummarized_messages) - (self.max_unsummarized_messages // 2)
        batch = self._unsummarized_messages[:num_to_summarize]
        self._unsummarized_messages = self._unsummarized_messages[num_to_summarize:]

        batch_lines = []
        for m in batch:
            role_name = m.role.value if isinstance(m.role, MessageRole) else str(m.role)
            batch_lines.append(f"{role_name.upper()}: {m.content}")
        batch_text = "\n".join(batch_lines)

        if self.provider is not None:
            try:
                prompt = (
                    f"Current summary:\n{self.summary}\n\n"
                    f"New conversation segment to integrate:\n{batch_text}\n\n"
                    "Provide an updated, concise summary preserving key facts, user requests, and tool findings."
                )
                response = self.provider.generate(
                    messages=[
                        Message(role=MessageRole.SYSTEM, content="You are a conversation summarizer."),
                        Message(role=MessageRole.USER, content=prompt),
                    ]
                )
                if response.content:
                    self.summary = response.content.strip()
                    return
            except Exception:
                pass

        # Offline / deterministic distillation fallback
        distilled = []
        if self.summary:
            distilled.append(self.summary)
        for m in batch:
            snippet = m.content[:100] + ("..." if len(m.content) > 100 else "")
            distilled.append(f"[{m.role.value}]: {snippet}")
        self.summary = " | ".join(distilled)

    def get_messages(self) -> List[Message]:
        result: List[Message] = []

        summary_text = f"{self.summary_prefix}\n{self.summary}" if self.summary else ""

        if self._system_message:
            if summary_text:
                combined = f"{self._system_message.content}\n\n{summary_text}"
                result.append(Message(role=MessageRole.SYSTEM, content=combined))
            else:
                result.append(self._system_message)
        elif summary_text:
            result.append(Message(role=MessageRole.SYSTEM, content=summary_text))

        result.extend(self._unsummarized_messages)
        return result

    def clear(self) -> None:
        self.summary = ""
        self._all_messages.clear()
        self._unsummarized_messages.clear()
        self._system_message = None


class SemanticMemory:
    """
    Pure-Python semantic & episodic memory store.
    Uses tokenization, term-frequency, and token-overlap similarity scoring
    for deterministic, fast relevance retrieval without C-extension dependencies.
    """

    def __init__(self):
        self._entries: Dict[str, Dict[str, Any]] = {}

    def _tokenize(self, text: str) -> Set[str]:
        words = re.findall(r"\w+", text.lower())
        # Filter standard short stop-words
        stop_words = {"the", "a", "an", "is", "in", "it", "to", "and", "or", "of", "for", "on", "with", "at"}
        return {w for w in words if w not in stop_words and len(w) > 1}

    def store(
        self,
        key: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store or update an episodic memory entry."""
        entry = {
            "key": key,
            "content": content,
            "tokens": self._tokenize(content),
            "metadata": metadata or {},
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._entries[key] = entry
        return key

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Get an entry by key."""
        entry = self._entries.get(key)
        if not entry:
            return None
        return {
            "key": entry["key"],
            "content": entry["content"],
            "metadata": entry["metadata"],
            "timestamp": entry["timestamp"],
        }

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Search entries ranked by token overlap / Jaccard-like similarity.
        Returns top_k items with score >= min_score.
        """
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored_results = []
        for key, entry in self._entries.items():
            entry_tokens = entry["tokens"]
            if not entry_tokens:
                continue

            intersection = query_tokens.intersection(entry_tokens)
            union = query_tokens.union(entry_tokens)
            score = len(intersection) / len(union) if union else 0.0

            # Substring exact match bonus
            if query.lower() in entry["content"].lower():
                score += 0.5

            if score >= min_score and score > 0.0:
                scored_results.append({
                    "key": key,
                    "content": entry["content"],
                    "metadata": entry["metadata"],
                    "timestamp": entry["timestamp"],
                    "score": round(score, 4),
                })

        scored_results.sort(key=lambda x: x["score"], reverse=True)
        return scored_results[:top_k]

    def delete(self, key: str) -> bool:
        """Delete an entry by key."""
        return self._entries.pop(key, None) is not None

    def list_all(self) -> List[Dict[str, Any]]:
        """List all entries."""
        return [
            {
                "key": e["key"],
                "content": e["content"],
                "metadata": e["metadata"],
                "timestamp": e["timestamp"],
            }
            for e in self._entries.values()
        ]

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
