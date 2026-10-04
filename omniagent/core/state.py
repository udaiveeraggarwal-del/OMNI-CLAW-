"""
State persistence layer for OmniAgent.
Provides canonical AgentState and pluggable storage backends:
InMemoryStateStore, FileStateStore, and SQLiteStateStore.
"""

from __future__ import annotations

import abc
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Union
import uuid

from omniagent.core.models import Message


class AgentState:
    """Canonical execution state for an agent session."""

    def __init__(
        self,
        session_id: str,
        status: str = "IDLE",
        messages: Optional[List[Message]] = None,
        plan: Optional[Any] = None,
        scratchpad: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ):
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.session_id = session_id
        self.status = status
        self.messages: List[Message] = messages if messages is not None else []
        self.plan = plan
        self.scratchpad: Dict[str, Any] = scratchpad if scratchpad is not None else {}
        self.metadata: Dict[str, Any] = metadata if metadata is not None else {}
        self.created_at = created_at or now_iso
        self.updated_at = updated_at or now_iso

    def touch(self) -> None:
        """Update last modified timestamp."""
        self.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def add_message(self, message: Message) -> None:
        """Append a message and update timestamp."""
        self.messages.append(message)
        self.touch()

    def update_scratchpad(self, key: str, value: Any) -> None:
        """Store intermediate execution artifacts."""
        self.scratchpad[key] = value
        self.touch()

    def to_dict(self) -> Dict[str, Any]:
        plan_dict = None
        if self.plan is not None:
            if hasattr(self.plan, "to_dict"):
                plan_dict = self.plan.to_dict()
            elif isinstance(self.plan, dict):
                plan_dict = self.plan
            else:
                plan_dict = str(self.plan)

        return {
            "session_id": self.session_id,
            "status": self.status,
            "messages": [m.to_dict() for m in self.messages],
            "plan": plan_dict,
            "scratchpad": self.scratchpad,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentState:
        raw_msgs = data.get("messages") or []
        messages = [
            Message.from_dict(m) if isinstance(m, dict) else m
            for m in raw_msgs
        ]
        return cls(
            session_id=str(data.get("session_id", "")),
            status=str(data.get("status", "IDLE")),
            messages=messages,
            plan=data.get("plan"),
            scratchpad=data.get("scratchpad") or {},
            metadata=data.get("metadata") or {},
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


class BaseStateStore(abc.ABC):
    """Abstract interface for agent state persistence."""

    @abc.abstractmethod
    def save(self, state: AgentState) -> None:
        """Persist or update state."""
        pass

    @abc.abstractmethod
    def load(self, session_id: str) -> Optional[AgentState]:
        """Load state by session ID, returning None if not found."""
        pass

    @abc.abstractmethod
    def delete(self, session_id: str) -> bool:
        """Delete state for session ID, returning True if deleted."""
        pass

    @abc.abstractmethod
    def list_sessions(self) -> List[str]:
        """Return list of all stored session IDs."""
        pass

    def exists(self, session_id: str) -> bool:
        """Check if session state exists."""
        return self.load(session_id) is not None


class InMemoryStateStore(BaseStateStore):
    """Ephemeral, in-memory state store for tests and short-lived tasks."""

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def save(self, state: AgentState) -> None:
        state.touch()
        with self._lock:
            # Store serialised copy to ensure isolation
            self._store[state.session_id] = state.to_dict()

    def load(self, session_id: str) -> Optional[AgentState]:
        with self._lock:
            data = self._store.get(session_id)
            if data is None:
                return None
            return AgentState.from_dict(data)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._store.pop(session_id, None) is not None

    def list_sessions(self) -> List[str]:
        with self._lock:
            return sorted(list(self._store.keys()))

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class FileStateStore(BaseStateStore):
    """Filesystem JSON state store."""

    def __init__(self, directory: Union[str, Path] = ".omniagent/sessions"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _file_path(self, session_id: str) -> Path:
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        if not safe_id:
            safe_id = f"sess_{hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:12]}"
        return self.directory / f"{safe_id}.json"

    def save(self, state: AgentState) -> None:
        state.touch()
        path = self._file_path(state.session_id)
        with self._lock:
            temp_path = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(state.to_dict(), f, indent=2)
                temp_path.replace(path)
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

    def load(self, session_id: str) -> Optional[AgentState]:
        path = self._file_path(session_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return AgentState.from_dict(data)
            except Exception:
                return None

    def delete(self, session_id: str) -> bool:
        path = self._file_path(session_id)
        with self._lock:
            if path.exists():
                path.unlink()
                return True
            return False

    def list_sessions(self) -> List[str]:
        with self._lock:
            if not self.directory.exists():
                return []
            sessions = []
            for p in self.directory.glob("*.json"):
                sessions.append(p.stem)
            return sorted(sessions)


class SQLiteStateStore(BaseStateStore):
    """Persistent SQLite state store supporting ACID transactions."""

    def __init__(self, db_path: str = "omniagent.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS agent_states (
                            session_id TEXT PRIMARY KEY,
                            status TEXT NOT NULL,
                            state_json TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_status ON agent_states(status)"
                    )

    def save(self, state: AgentState) -> None:
        state.touch()
        state_dict = state.to_dict()
        state_json = json.dumps(state_dict)

        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO agent_states (session_id, status, state_json, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                            status = excluded.status,
                            state_json = excluded.state_json,
                            updated_at = excluded.updated_at
                        """,
                        (state.session_id, state.status, state_json, state.updated_at),
                    )

    def load(self, session_id: str) -> Optional[AgentState]:
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT state_json FROM agent_states WHERE session_id = ?",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                try:
                    data = json.loads(row["state_json"])
                    return AgentState.from_dict(data)
                except Exception:
                    return None

    def delete(self, session_id: str) -> bool:
        with self._lock:
            with self._connection() as conn:
                with conn:
                    cursor = conn.execute(
                        "DELETE FROM agent_states WHERE session_id = ?",
                        (session_id,),
                    )
                    return cursor.rowcount > 0

    def list_sessions(self) -> List[str]:
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT session_id FROM agent_states ORDER BY updated_at DESC"
                )
                return [row["session_id"] for row in cursor.fetchall()]
