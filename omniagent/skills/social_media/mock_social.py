"""Explicit in-memory social platform adapter for demos and offline tests."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict


class InMemorySocialAdapter:
    """Fake provider; it does not contact Instagram, YouTube, or a scheduler."""

    def __init__(self) -> None:
        self.posts: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def publish(self, platform: str, post: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            post_id = uuid.uuid4().hex
            self.posts[post_id] = {**post, "platform": platform, "status": "published"}
            return {"post_id": post_id, "platform": platform, "status": "published", "mock": True}

    def schedule(self, platform: str, post: Dict[str, Any], publish_at: str) -> Dict[str, Any]:
        with self._lock:
            post_id = uuid.uuid4().hex
            self.posts[post_id] = {**post, "platform": platform, "status": "scheduled", "publish_at": publish_at}
            return {"post_id": post_id, "platform": platform, "status": "scheduled", "publish_at": publish_at, "mock": True}

    def analytics(self, platform: str, post_id: str) -> Dict[str, Any]:
        if post_id not in self.posts or self.posts[post_id]["platform"] != platform:
            raise ValueError("Mock post was not found.")
        return {"post_id": post_id, "platform": platform, "metrics": {}, "mock": True}

