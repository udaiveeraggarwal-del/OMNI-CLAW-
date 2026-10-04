"""Host-issued capability grants for machine and external-account actions.

The LLM cannot mint or widen a grant: hosts create this object only after
their own consent UI has authorized the requested scope. Keep it in the trusted
runtime and never serialize the grant secret into prompts or tool arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import FrozenSet, Iterable, Optional
import secrets


@dataclass(frozen=True)
class HostCapabilityGrant:
    """An expiring, host-created authorization for a fixed set of operations."""

    grant_id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    scopes: FrozenSet[str] = frozenset()
    full_machine: bool = False
    filesystem_roots: tuple[str, ...] = ()
    process_allowlist: FrozenSet[str] = frozenset()
    expires_at: Optional[datetime] = None

    @classmethod
    def issue(
        cls,
        *,
        scopes: Iterable[str] = (),
        full_machine: bool = False,
        filesystem_roots: Iterable[str] = (),
        process_allowlist: Iterable[str] = (),
        ttl_seconds: int = 900,
    ) -> "HostCapabilityGrant":
        """Create a grant from trusted host code, never from model-supplied data."""
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 86400:
            raise ValueError("ttl_seconds must be between 1 second and 24 hours.")
        normalized_scopes = frozenset(_validate_scope(scope) for scope in scopes)
        normalized_roots = tuple(str(Path(root).expanduser().resolve()) for root in filesystem_roots)
        normalized_processes = frozenset(str(name).casefold() for name in process_allowlist)
        now = datetime.now(timezone.utc)
        return cls(
            scopes=normalized_scopes,
            full_machine=bool(full_machine),
            filesystem_roots=normalized_roots,
            process_allowlist=normalized_processes,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    def is_valid(self) -> bool:
        return self.expires_at is None or datetime.now(timezone.utc) < self.expires_at

    def allows(self, scope: str) -> bool:
        if not self.is_valid():
            return False
        if scope in self.scopes:
            return True
        if self.full_machine and scope.startswith("machine."):
            return True
        return False

    def allows_path(self, path: str | Path) -> bool:
        if not self.is_valid():
            return False
        candidate = Path(path).expanduser().resolve(strict=False)
        if self.full_machine:
            return True
        for root in self.filesystem_roots:
            try:
                candidate.relative_to(Path(root))
                return True
            except ValueError:
                continue
        return False

    def allows_process(self, executable: str) -> bool:
        if not self.is_valid():
            return False
        if self.allows("machine.process.any"):
            return True
        return Path(executable).name.casefold() in self.process_allowlist


def _validate_scope(scope: str) -> str:
    if not isinstance(scope, str) or not scope or len(scope) > 100 or any(ch.isspace() for ch in scope):
        raise ValueError("capability scopes must be non-empty identifiers without whitespace.")
    return scope
