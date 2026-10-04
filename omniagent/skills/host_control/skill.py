"""Host machine operations enabled only by an explicit host-issued grant."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Protocol

from omniagent.core.models import ToolResult
from omniagent.core.router import BaseTool
from omniagent.security.capabilities import HostCapabilityGrant
from omniagent.skills.base import BaseSkill


class HostControlError(RuntimeError):
    """Host machine operation was not authorized or could not be completed."""


class DesktopBackend(Protocol):
    """Bridge to a host UI automation runtime, such as Windows Computer Use."""

    def execute(self, action: str, arguments: Dict[str, Any]) -> Any: ...


class HostControlTool(BaseTool):
    _ACTIONS = {"list_directory", "read_file", "write_file", "run_process", "desktop"}

    def __init__(self, grant: Optional[HostCapabilityGrant], desktop_backend: Optional[DesktopBackend] = None):
        self.grant = grant
        self.desktop_backend = desktop_backend

    @property
    def name(self) -> str:
        return "host.control"

    @property
    def description(self) -> str:
        return (
            "Use host-authorized filesystem, process, and desktop capabilities. A host must issue a valid "
            "machine-control grant before this action can affect the computer."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": sorted(self._ACTIONS)},
                "path": {"type": "string", "maxLength": 4096},
                "content": {"type": "string", "maxLength": 2000000},
                "command": {"type": "array", "minItems": 1, "maxItems": 64, "items": {"type": "string", "maxLength": 4096}},
                "working_directory": {"type": "string", "maxLength": 4096},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                "desktop_action": {"type": "string", "maxLength": 80},
                "desktop_arguments": {"type": "object", "maxProperties": 32},
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            action = kwargs["action"]
            if action not in self._ACTIONS:
                raise HostControlError("Unsupported host action.")
            if action == "desktop":
                self._require("machine.ui")
                if self.desktop_backend is None:
                    raise HostControlError("No host desktop automation bridge is connected.")
                desktop_action = kwargs.get("desktop_action", "")
                if not desktop_action or len(desktop_action) > 80:
                    raise HostControlError("desktop_action is required.")
                result = self.desktop_backend.execute(desktop_action, kwargs.get("desktop_arguments", {}))
                return ToolResult(success=True, output=result)

            if action == "run_process":
                return ToolResult(success=True, output=self._run_process(kwargs))
            path = self._authorized_path(kwargs.get("path", ""), write=action == "write_file")
            if action == "list_directory":
                if not path.is_dir():
                    raise HostControlError("path is not a directory.")
                entries = []
                for item in list(path.iterdir())[:500]:
                    entries.append({"name": item.name, "is_directory": item.is_dir(), "is_file": item.is_file()})
                return ToolResult(success=True, output={"path": str(path), "entries": entries})
            if action == "read_file":
                self._require("machine.fs.read")
                if not path.is_file() or path.stat().st_size > 2_000_000:
                    raise HostControlError("File is missing or exceeds the 2 MB read limit.")
                return ToolResult(success=True, output={"path": str(path), "content": path.read_text(encoding="utf-8", errors="replace")})
            if action == "write_file":
                self._require("machine.fs.write")
                content = kwargs.get("content")
                if not isinstance(content, str) or len(content.encode("utf-8")) > 2_000_000:
                    raise HostControlError("content must be text no larger than 2 MB.")
                path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic replace avoids leaving a partial file if the host process is interrupted.
                temporary_name = None
                try:
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                        temporary_name = handle.name
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    Path(temporary_name).replace(path)
                finally:
                    if temporary_name and Path(temporary_name).exists():
                        Path(temporary_name).unlink(missing_ok=True)
                return ToolResult(success=True, output={"path": str(path), "bytes_written": len(content.encode("utf-8"))})
            raise HostControlError("Unsupported host action.")
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))

    def _require(self, scope: str) -> None:
        if self.grant is None or not self.grant.allows(scope):
            raise HostControlError(f"Host capability '{scope}' was not granted or has expired.")

    def _authorized_path(self, raw_path: str, write: bool) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 4096:
            raise HostControlError("An absolute path is required.")
        scope = "machine.fs.write" if write else "machine.fs.read"
        self._require(scope)
        path = Path(raw_path).expanduser().resolve(strict=False)
        if self.grant is None or not self.grant.allows_path(path):
            raise HostControlError("The requested path is outside the host grant's filesystem scope.")
        return path

    def _run_process(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self._require("machine.process")
        command = arguments.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(item, str) or "\x00" in item for item in command):
            raise HostControlError("command must be an argv array of text values; shell strings are not accepted.")
        if len(command) > 64 or any(len(item) > 4096 for item in command):
            raise HostControlError("command exceeds the configured argument limits.")
        executable = shutil.which(command[0]) or command[0]
        if not self.grant or not self.grant.allows_process(executable):
            raise HostControlError("The executable is outside the host grant's process scope.")
        timeout = arguments.get("timeout_seconds", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise HostControlError("timeout_seconds must be between 1 and 120.")
        cwd = None
        if arguments.get("working_directory"):
            cwd_path = self._authorized_path(arguments["working_directory"], write=False)
            if not cwd_path.is_dir():
                raise HostControlError("working_directory is not a directory.")
            cwd = str(cwd_path)
        # Pass only ordinary runtime variables. API keys and other secrets from the
        # agent host process are intentionally not inherited by launched programs.
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT") if key in os.environ}
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
                close_fds=True,
                creationflags=creationflags,
                start_new_session=(os.name != "nt"),
            )
            try:
                process.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                timed_out = True
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(65536).decode("utf-8", errors="replace")
            stderr = stderr_file.read(65536).decode("utf-8", errors="replace")
            return {
                "argv": command,
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": timed_out,
                "output_truncated": stdout_file.tell() >= 65536 or stderr_file.tell() >= 65536,
            }


class HostControlSkill(BaseSkill):
    def __init__(self, grant: Optional[HostCapabilityGrant] = None, desktop_backend: Optional[DesktopBackend] = None):
        self.grant = grant
        self.desktop_backend = desktop_backend

    @property
    def skill_id(self) -> str:
        return "host"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Control local files, processes, and desktop apps within a host-issued capability grant."

    def get_tools(self) -> List[BaseTool]:
        return [HostControlTool(self.grant, self.desktop_backend)]

