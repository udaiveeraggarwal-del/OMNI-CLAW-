"""Restricted Python and JavaScript execution through Docker.

There is deliberately no subprocess fallback: running model-generated code
directly on the host would not provide a meaningful security boundary. The
container is run without network access, with a read-only root filesystem,
reduced privileges, bounded CPU/memory/process count, and capped output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from omniagent.core.models import ToolResult
from omniagent.core.router import BaseTool
from omniagent.skills.base import BaseSkill


class SandboxUnavailableError(RuntimeError):
    """Raised when the required isolated execution backend is unavailable."""


@dataclass(frozen=True)
class SandboxPolicy:
    """Host-enforced resource limits for each isolated execution."""

    max_timeout_seconds: int = 30
    max_code_bytes: int = 100_000
    max_output_bytes: int = 65_536
    memory_limit: str = "128m"
    cpu_limit: str = "0.5"
    process_limit: int = 64
    temp_limit: str = "16m"

    def __post_init__(self) -> None:
        if not 1 <= self.max_timeout_seconds <= 120:
            raise ValueError("max_timeout_seconds must be between 1 and 120")
        if not 1_024 <= self.max_code_bytes <= 1_000_000:
            raise ValueError("max_code_bytes must be between 1024 and 1000000")
        if not 1_024 <= self.max_output_bytes <= 1_000_000:
            raise ValueError("max_output_bytes must be between 1024 and 1000000")
        if not 1 <= self.process_limit <= 256:
            raise ValueError("process_limit must be between 1 and 256")


class DockerSandboxBackend:
    """Execute code in a local Docker container with a restrictive policy.

    Images must already exist locally. The pull-never setting prevents code
    execution from implicitly contacting a registry. For stronger multi-tenant
    isolation, deploy a dedicated microVM backend rather than sharing a Docker
    daemon.
    """

    DEFAULT_IMAGES = {
        "python": "python:3.12-alpine",
        "javascript": "node:22-alpine",
    }
    _SECRET_PATTERNS = (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    )

    def __init__(
        self,
        policy: SandboxPolicy | None = None,
        docker_executable: str | None = None,
        images: Dict[str, str] | None = None,
    ) -> None:
        self.policy = policy or SandboxPolicy()
        self.docker_executable = docker_executable or shutil.which("docker")
        self.images = dict(images or self.DEFAULT_IMAGES)
        for language in ("python", "javascript"):
            image = self.images.get(language)
            if not image or image.startswith("-") or any(ch.isspace() for ch in image):
                raise ValueError(f"invalid Docker image configured for {language}")

    def run(self, language: str, code: str, timeout_seconds: int = 15) -> Dict[str, Any]:
        if language not in self.images:
            raise ValueError("language must be python or javascript")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must be a non-empty string")
        if len(code.encode("utf-8")) > self.policy.max_code_bytes:
            raise ValueError("code exceeds the configured size limit")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise ValueError("timeout_seconds must be an integer")
        if not 1 <= timeout_seconds <= self.policy.max_timeout_seconds:
            raise ValueError(
                f"timeout_seconds must be between 1 and {self.policy.max_timeout_seconds}"
            )
        if not self.docker_executable:
            raise SandboxUnavailableError(
                "Docker is not installed or is not available on PATH; host execution is disabled."
            )

        extension = "py" if language == "python" else "js"
        command = ["python", "/workspace/main.py"] if language == "python" else [
            "node",
            "--disable-proto=throw",
            "/workspace/main.js",
        ]

        with tempfile.TemporaryDirectory(prefix="omniagent-sandbox-") as temporary_dir:
            root = Path(temporary_dir)
            source_file = root / f"main.{extension}"
            source_file.write_text(code, encoding="utf-8", newline="\n")
            try:
                source_file.chmod(0o444)
            except OSError:
                # Docker's read-only bind mount remains the enforcement layer
                # on hosts whose filesystem does not implement POSIX modes.
                pass

            cidfile = root / "container-id"
            docker_command = [
                self.docker_executable,
                "run",
                "--pull=never",
                "--rm",
                "--init",
                f"--cidfile={cidfile}",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=65534:65534",
                f"--memory={self.policy.memory_limit}",
                f"--memory-swap={self.policy.memory_limit}",
                f"--cpus={self.policy.cpu_limit}",
                f"--pids-limit={self.policy.process_limit}",
                "--ulimit",
                "fsize=1048576:1048576",
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,size={self.policy.temp_limit}",
                "--mount",
                f"type=bind,source={root},target=/workspace,readonly",
                "--workdir=/workspace",
                self.images[language],
                *command,
            ]
            try:
                process = subprocess.Popen(
                    docker_command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                )
            except OSError as exc:
                raise SandboxUnavailableError(f"Could not start Docker: {exc}") from exc

            output_lock = threading.Lock()
            output_size = 0
            output_limit_hit = threading.Event()
            buffers = {"stdout": bytearray(), "stderr": bytearray()}

            def drain(stream: Any, label: str) -> None:
                nonlocal output_size
                try:
                    while True:
                        chunk = stream.read(4096)
                        if not chunk:
                            return
                        with output_lock:
                            remaining = self.policy.max_output_bytes - output_size
                            if remaining > 0:
                                retained = chunk[:remaining]
                                buffers[label].extend(retained)
                                output_size += len(retained)
                            if len(chunk) > max(remaining, 0):
                                output_limit_hit.set()
                finally:
                    stream.close()

            readers = [
                threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
                threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
            ]
            for reader in readers:
                reader.start()

            stop_reason: Optional[str] = None
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if output_limit_hit.is_set():
                    stop_reason = "output_limit"
                    self._stop_container(process, cidfile)
                    break
                if time.monotonic() >= deadline:
                    stop_reason = "timeout"
                    self._stop_container(process, cidfile)
                    break
                time.sleep(0.02)

            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            for reader in readers:
                reader.join(timeout=3)

            if not cidfile.exists() and process.returncode != 0 and stop_reason is None:
                details = self._redact(bytes(buffers["stderr"]).decode("utf-8", errors="replace"))
                raise SandboxUnavailableError(
                    "Docker could not start the sandbox. Confirm Docker is running and "
                    f"the configured image is already present locally. {details[-1000:]}"
                )

            report = {
                "language": language,
                "exit_code": process.returncode,
                "stdout": self._redact(bytes(buffers["stdout"]).decode("utf-8", errors="replace")),
                "stderr": self._redact(bytes(buffers["stderr"]).decode("utf-8", errors="replace")),
                "timed_out": stop_reason == "timeout",
                "output_limit_reached": stop_reason == "output_limit",
                "timeout_seconds": timeout_seconds,
                "output_truncated": stop_reason == "output_limit",
                "sandbox": {
                    "runtime": "docker",
                    "network": "disabled",
                    "filesystem": "read-only except bounded temporary storage",
                    "memory_limit": self.policy.memory_limit,
                    "cpu_limit": self.policy.cpu_limit,
                },
            }
            return report

    def _stop_container(self, process: subprocess.Popen[Any], cidfile: Path) -> None:
        try:
            container_id = cidfile.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            container_id = ""
        if re.fullmatch(r"[0-9a-fA-F]{12,64}", container_id):
            try:
                subprocess.run(
                    [self.docker_executable or "docker", "kill", container_id],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

    @classmethod
    def _redact(cls, text: str) -> str:
        for pattern in cls._SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text


class RunCodeTool(BaseTool):
    def __init__(self, backend: DockerSandboxBackend | None = None) -> None:
        self.backend = backend or DockerSandboxBackend()

    @property
    def name(self) -> str:
        return "code_runner.run"

    @property
    def description(self) -> str:
        return (
            "Run a short Python or JavaScript snippet in a Docker sandbox with no network, "
            "resource limits, and bounded output. Fails closed if Docker is unavailable."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "javascript"]},
                "code": {"type": "string", "minLength": 1, "maxLength": 100000},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["language", "code"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            report = self.backend.run(
                language=kwargs.get("language", ""),
                code=kwargs.get("code", ""),
                timeout_seconds=kwargs.get("timeout_seconds", 15),
            )
        except (ValueError, SandboxUnavailableError) as exc:
            return ToolResult(success=False, output=None, error=str(exc))

        if report["timed_out"]:
            return ToolResult(success=False, output=report, error="Code execution exceeded its time limit.")
        if report["output_limit_reached"]:
            return ToolResult(success=False, output=report, error="Code execution exceeded its output limit.")
        return ToolResult(success=True, output=report)


class CodeRunnerSkill(BaseSkill):
    def __init__(self, backend: DockerSandboxBackend | None = None) -> None:
        self.backend = backend

    @property
    def skill_id(self) -> str:
        return "code_runner"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Run short Python and JavaScript tasks in a network-disabled Docker sandbox."

    def get_tools(self) -> List[BaseTool]:
        return [RunCodeTool(self.backend)]
