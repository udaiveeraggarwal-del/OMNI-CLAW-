"""
Pytest configuration, shared fixtures, and contract oracles for OmniAgent E2E test suite.
"""

from __future__ import annotations

import os
import sys
import json
import socket
import tempfile
import threading
import time
from typing import Any, Dict, Generator, List, Optional
import unittest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# Sample canonical payloads for testing
SAMPLE_MESSAGES = [
    {"role": "system", "content": "You are OmniAgent, a helpful assistant."},
    {"role": "user", "content": "Analyze the quarterly financial metrics."},
    {"role": "assistant", "content": "I will retrieve the financial metrics now."},
]

SAMPLE_TOOL_DEFINITIONS = [
    {
        "name": "data_fetcher",
        "description": "Fetches data from an internal or external source",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["source"],
        },
    },
    {
        "name": "data_analyzer",
        "description": "Analyzes raw dataset and computes summary metrics",
        "parameters": {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "number"}},
                "metric": {"type": "string", "enum": ["mean", "sum", "max", "min"]},
            },
            "required": ["data", "metric"],
        },
    },
]

SAMPLE_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>OmniAgent High Performance Automation</title>
    <meta name="description" content="OmniAgent is a universal autonomous agent framework.">
    <meta name="keywords" content="AI, agent, autonomous, automation, LLM">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "OmniAgent",
        "operatingSystem": "All",
        "applicationCategory": "BusinessApplication"
    }
    </script>
</head>
<body>
    <h1>Welcome to OmniAgent</h1>
    <h2>Autonomous Multi-Step Workflows</h2>
    <p>Deploy intelligent agents across web operations, code generation, and marketing automation.</p>
    <p>OmniAgent empowers businesses with multi-provider LLM intelligence and secure tunneling.</p>
    <a href="/features">Explore Features</a>
</body>
</html>
"""

SAMPLE_WORKFLOW_SCHEMA = {
    "schema_version": "1.0.0",
    "metadata": {
        "name": "Research & Synthesis Pipeline",
        "description": "Autonomous web research and executive summary pipeline",
        "version": "1.0.0",
    },
    "nodes": [
        {
            "id": "node_trigger_1",
            "type": "trigger",
            "subtype": "manual",
            "position": {"x": 100, "y": 150},
            "config": {"default_input": "Research competitor SEO metrics"},
        },
        {
            "id": "node_llm_1",
            "type": "llm",
            "subtype": "reasoning",
            "position": {"x": 350, "y": 150},
            "config": {
                "provider": "mock",
                "model": "omni-reasoner-v1",
                "temperature": 0.2,
                "prompt": "Analyze input query: {{input}}",
            },
        },
        {
            "id": "node_tool_1",
            "type": "tool",
            "subtype": "seo_optimizer",
            "position": {"x": 650, "y": 150},
            "config": {"action": "audit_page"},
        },
        {
            "id": "node_action_1",
            "type": "action",
            "subtype": "synthesize",
            "position": {"x": 900, "y": 150},
            "config": {"format": "json"},
        },
    ],
    "edges": [
        {
            "id": "edge_1",
            "source": "node_trigger_1",
            "source_port": "out",
            "target": "node_llm_1",
            "target_port": "in",
        },
        {
            "id": "edge_2",
            "source": "node_llm_1",
            "source_port": "out",
            "target": "node_tool_1",
            "target_port": "in",
        },
        {
            "id": "edge_3",
            "source": "node_tool_1",
            "source_port": "out",
            "target": "node_action_1",
            "target_port": "in",
        },
    ],
}


class MockSOCKS5Server:
    """
    Lightweight RFC 1928 SOCKS5 loopback test server for E2E tunneling verification.
    Runs on an ephemeral port on 127.0.0.1.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, port))
        self.port = self.server_sock.getsockname()[1]
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.connections_handled = 0
        self.last_target_host: Optional[str] = None
        self.last_target_port: Optional[int] = None

    def start(self):
        self.running = True
        self.server_sock.listen(5)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        try:
            self.server_sock.close()
        except Exception:
            pass
        if self.thread:
            self.thread.join(timeout=1.0)

    def _serve(self):
        try:
            self.server_sock.settimeout(0.5)
        except Exception:
            return
        while self.running:
            try:
                client_sock, _ = self.server_sock.accept()
                t = threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_client(self, sock: socket.socket):
        try:
            sock.settimeout(3.0)
            # 1. Version identifier / method selection message
            ver_methods = sock.recv(2)
            if not ver_methods or ver_methods[0] != 0x05:
                sock.close()
                return
            nmethods = ver_methods[1]
            methods = sock.recv(nmethods)
            # Respond: NO AUTHENTICATION REQUIRED (0x00)
            sock.sendall(b"\x05\x00")

            # 2. Connection request
            req = sock.recv(4)
            if len(req) < 4 or req[0] != 0x05 or req[1] != 0x01:  # CONNECT
                sock.close()
                return

            atyp = req[3]
            target_host = ""
            if atyp == 0x01:  # IPv4
                ip_bytes = sock.recv(4)
                target_host = socket.inet_ntoa(ip_bytes)
            elif atyp == 0x03:  # Domain name (Remote DNS Resolution)
                dlen_byte = sock.recv(1)
                dlen = dlen_byte[0]
                target_host = sock.recv(dlen).decode("utf-8", errors="replace")
            elif atyp == 0x04:  # IPv6
                ip6_bytes = sock.recv(16)
                target_host = socket.inet_ntop(socket.AF_INET6, ip6_bytes)

            port_bytes = sock.recv(2)
            target_port = int.from_bytes(port_bytes, "big")

            self.last_target_host = target_host
            self.last_target_port = target_port
            self.connections_handled += 1

            # Reply: Success (0x00)
            reply = b"\x05\x00\x00\x01\x7f\x00\x00\x01" + port_bytes
            sock.sendall(reply)

            # Echo or acknowledge data
            data = sock.recv(1024)
            if data:
                # Mock response
                sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOmniAgent Proxy OK\n")
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass


# Pytest fixtures if pytest is loaded
try:
    import pytest

    @pytest.fixture
    def socks5_server() -> Generator[MockSOCKS5Server, None, None]:
        srv = MockSOCKS5Server()
        srv.start()
        try:
            yield srv
        finally:
            srv.stop()

    @pytest.fixture
    def temp_dir() -> Generator[str, None, None]:
        with tempfile.TemporaryDirectory() as td:
            yield td

    @pytest.fixture
    def sample_workflow() -> Dict[str, Any]:
        return dict(SAMPLE_WORKFLOW_SCHEMA)

except ImportError:
    pass
