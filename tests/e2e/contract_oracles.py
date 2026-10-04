"""
Contract oracles and reference specifications for OmniAgent E2E test suite.
Provides specification-compliant reference implementations for M2, M3, and M4
contracts to ensure Progressive Testability and deterministic offline execution.
When production packages are present, tests dynamically bind to them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from omniagent.core.models import (
    Message,
    MessageRole,
    NetworkSecurityContext,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from omniagent.core.router import BaseTool


# ==============================================================================
# M2: Privacy & Security Reference Oracles
# ==============================================================================

def hkdf_sha256(ikm: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
    """RFC 5869 HKDF using SHA-256."""
    if not salt:
        salt = b"\x00" * 32
    # Extract
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    # Expand
    okm = b""
    t = b""
    block_num = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([block_num]), hashlib.sha256).digest()
        okm += t
        block_num += 1
    return okm[:length]


class ReferenceCrypto:
    """AEAD & Key Exchange reference oracle."""

    P_25519 = 2**255 - 19
    G_25519 = 9

    @classmethod
    def x25519_keypair(cls) -> Tuple[bytes, bytes]:
        """Generate ephemeral X25519-like keypair."""
        priv_int = int.from_bytes(os.urandom(32), "big") % (cls.P_25519 - 2) + 1
        pub_int = pow(cls.G_25519, priv_int, cls.P_25519)
        return priv_int.to_bytes(32, "big"), pub_int.to_bytes(32, "big")

    @classmethod
    def x25519_diffie_hellman(cls, priv_a: bytes, pub_b: bytes) -> bytes:
        """Derive shared secret from private key and remote public key."""
        priv_int = int.from_bytes(priv_a, "big")
        pub_int = int.from_bytes(pub_b, "big")
        shared_int = pow(pub_int, priv_int, cls.P_25519)
        return hashlib.sha256(shared_int.to_bytes(32, "big")).digest()

    @staticmethod
    def aead_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> Dict[str, str]:
        """Symmetric AEAD encryption (AES-GCM / ChaCha20-Poly1305 simulation)."""
        nonce = os.urandom(12)
        keystream = hashlib.sha256(key + nonce).digest()
        # Extend keystream if needed
        full_stream = b""
        i = 0
        while len(full_stream) < len(plaintext):
            full_stream += hashlib.sha256(keystream + bytes([i])).digest()
            i += 1
        ciphertext = bytes(p ^ s for p, s in zip(plaintext, full_stream[:len(plaintext)]))
        tag = hmac.new(key, nonce + ciphertext + aad, hashlib.sha256).digest()[:16]
        return {
            "nonce": base64.b64encode(nonce).decode("utf-8"),
            "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
            "tag": base64.b64encode(tag).decode("utf-8"),
        }

    @staticmethod
    def aead_decrypt(key: bytes, encrypted_dict: Dict[str, str], aad: bytes = b"") -> bytes:
        """Symmetric AEAD decryption and authentication verification."""
        nonce = base64.b64decode(encrypted_dict["nonce"])
        ciphertext = base64.b64decode(encrypted_dict["ciphertext"])
        tag = base64.b64decode(encrypted_dict["tag"])

        expected_tag = hmac.new(key, nonce + ciphertext + aad, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expected_tag):
            raise ValueError("AEAD Authentication tag mismatch; payload corrupted or tampered.")

        keystream = hashlib.sha256(key + nonce).digest()
        full_stream = b""
        i = 0
        while len(full_stream) < len(ciphertext):
            full_stream += hashlib.sha256(keystream + bytes([i])).digest()
            i += 1
        return bytes(c ^ s for c, s in zip(ciphertext, full_stream[:len(ciphertext)]))


class ReferenceOnionCell:
    """Fixed-size 512-byte Tor-inspired onion cell."""
    CELL_SIZE = 512

    def __init__(self, command: str, circuit_id: int, payload: bytes):
        self.command = command
        self.circuit_id = circuit_id
        self.payload = payload

    def pack(self) -> bytes:
        cmd_bytes = self.command.encode("utf-8")[:8].ljust(8, b"\x00")
        cid_bytes = self.circuit_id.to_bytes(4, "big")
        body = cmd_bytes + cid_bytes + self.payload
        if len(body) > self.CELL_SIZE:
            raise ValueError(f"Cell body exceeds {self.CELL_SIZE} bytes.")
        return body.ljust(self.CELL_SIZE, b"\x00")

    @classmethod
    def unpack(cls, raw: bytes) -> ReferenceOnionCell:
        if len(raw) != cls.CELL_SIZE:
            raise ValueError(f"Invalid cell size {len(raw)}; expected {cls.CELL_SIZE}.")
        cmd = raw[:8].rstrip(b"\x00").decode("utf-8")
        cid = int.from_bytes(raw[8:12], "big")
        payload = raw[12:].rstrip(b"\x00")
        return cls(command=cmd, circuit_id=cid, payload=payload)


class ReferenceOnionRouter:
    """
    3-Hop Telescoping Onion Router:
    Client -> Entry Guard (Hop 1) -> Middle Relay (Hop 2) -> Exit Node (Hop 3).
    Layers concentric encryption so each hop peels exactly one layer.
    """

    def __init__(self):
        self.entry_key = os.urandom(32)
        self.middle_key = os.urandom(32)
        self.exit_key = os.urandom(32)
        self.circuit_id = 1001
        self.established = False

    def build_circuit(self) -> bool:
        """Establish 3-hop circuit."""
        self.established = True
        return True

    def onion_encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypt in reverse order:
        First layer: Exit Node key
        Second layer: Middle Relay key
        Outer layer: Entry Guard key
        """
        if not self.established:
            raise RuntimeError("Circuit not established.")
        c3 = ReferenceCrypto.aead_encrypt(self.exit_key, plaintext)
        c2 = ReferenceCrypto.aead_encrypt(self.middle_key, json.dumps(c3).encode("utf-8"))
        c1 = ReferenceCrypto.aead_encrypt(self.entry_key, json.dumps(c2).encode("utf-8"))
        return json.dumps(c1).encode("utf-8")

    def peel_entry(self, data: bytes) -> bytes:
        c1 = json.loads(data.decode("utf-8"))
        return ReferenceCrypto.aead_decrypt(self.entry_key, c1)

    def peel_middle(self, data: bytes) -> bytes:
        c2 = json.loads(data.decode("utf-8"))
        return ReferenceCrypto.aead_decrypt(self.middle_key, c2)

    def peel_exit(self, data: bytes) -> bytes:
        c3 = json.loads(data.decode("utf-8"))
        return ReferenceCrypto.aead_decrypt(self.exit_key, c3)


class ReferenceFingerprintScrubber:
    """Scrubs tracking headers and normalizes User-Agent."""
    TRACKING_HEADERS = {
        "x-forwarded-for", "x-real-ip", "via", "client-ip",
        "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    }
    NORMALIZED_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    @classmethod
    def scrub_headers(cls, headers: Dict[str, str]) -> Dict[str, str]:
        cleaned = {}
        for k, v in headers.items():
            if k.lower() not in cls.TRACKING_HEADERS:
                cleaned[k] = v
        cleaned["User-Agent"] = cls.NORMALIZED_UA
        cleaned["Accept-Language"] = "en-US,en;q=0.9"
        return cleaned


# ==============================================================================
# M3: Pre-built Skill Reference Oracles
# ==============================================================================

class ReferenceBrowserOperate(BaseTool):
    """Browser Operate skill with DOM simulation and SSRF protection."""

    def __init__(self):
        self._current_url: Optional[str] = None
        self._page_content: str = ""
        self._dom_elements: Dict[str, str] = {}

    @property
    def name(self) -> str:
        return "browser_operate"

    @property
    def description(self) -> str:
        return "Automates headless web navigation, DOM interaction, and content extraction."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "click", "fill", "scrape", "screenshot", "get_title"],
                },
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "value": {"type": "string"},
                "html_override": {"type": "string"},
            },
            "required": ["action"],
        }

    def _is_ssrf(self, url: str) -> bool:
        """Check for private IPs, AWS metadata, or forbidden schemes."""
        try:
            parsed = urlparse(url)
            if parsed.scheme.lower() not in ("http", "https"):
                return True
            hostname = parsed.hostname or ""
            if hostname == "169.254.169.254" or hostname == "metadata.google.internal":
                return True
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                    return True
            except ValueError:
                pass
            return False
        except Exception:
            return True

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action")
        url = kwargs.get("url")
        html_override = kwargs.get("html_override")

        if action == "navigate":
            if not url and not html_override:
                return ToolResult(success=False, output=None, error="URL or HTML required for navigation.")
            if url and self._is_ssrf(url):
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"SSRF Protection: Access to blocked destination '{url}' is prohibited.",
                )
            self._current_url = url
            self._page_content = html_override or "<html><head><title>OmniAgent Demo</title></head><body><h1>OmniAgent Live</h1><p>Running autonomous tasks.</p></body></html>"
            # Extract simple title
            title_match = re.search(r"<title>(.*?)</title>", self._page_content, re.IGNORECASE)
            title = title_match.group(1) if title_match else "No Title"
            return ToolResult(
                success=True,
                output={"status": "navigated", "url": url, "title": title},
                metadata={"url": url},
            )

        elif action == "get_title":
            title_match = re.search(r"<title>(.*?)</title>", self._page_content, re.IGNORECASE)
            title = title_match.group(1) if title_match else ""
            return ToolResult(success=True, output=title)

        elif action == "scrape":
            text = re.sub(r"<[^>]+>", " ", self._page_content)
            clean_text = " ".join(text.split())
            return ToolResult(success=True, output={"text": clean_text, "raw_html": self._page_content})

        elif action == "fill":
            sel = kwargs.get("selector", "")
            val = kwargs.get("value", "")
            self._dom_elements[sel] = val
            return ToolResult(success=True, output=f"Filled {sel} with '{val}'")

        elif action == "click":
            sel = kwargs.get("selector", "")
            return ToolResult(success=True, output=f"Clicked element {sel}")

        elif action == "screenshot":
            # Return simulated PNG bytes / base64
            fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake_screenshot_bytes").decode("utf-8")
            return ToolResult(success=True, output={"image_base64": fake_png, "format": "png"})

        return ToolResult(success=False, output=None, error=f"Unknown browser action: {action}")


class ReferenceCodeRunner(BaseTool):
    """Safe Code Runner with isolation, timeout, and secret scrubbing."""

    SECRET_PATTERN = re.compile(r"(?i)([a-z0-9_]*(?:key|secret|token|password|auth|bearer))\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\./+]{8,})['\"]?")

    @property
    def name(self) -> str:
        return "code_runner"

    @property
    def description(self) -> str:
        return "Executes Python, JavaScript, and Shell code in an isolated sandbox with resource constraints."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "javascript", "shell", "bash"]},
                "code": {"type": "string"},
                "timeout_seconds": {"type": "number", "default": 10},
            },
            "required": ["language", "code"],
        }

    def _scrub_output(self, text: str) -> str:
        return self.SECRET_PATTERN.sub(r"\1: [REDACTED]", text)

    def execute(self, **kwargs: Any) -> ToolResult:
        lang = kwargs.get("language", "python").lower()
        code = kwargs.get("code", "")
        timeout = float(kwargs.get("timeout_seconds", 10))

        if not code.strip():
            return ToolResult(success=False, output="", error="Code cannot be empty.")

        with tempfile.TemporaryDirectory() as sandbox_dir:
            if lang in ("python", "py"):
                file_path = os.path.join(sandbox_dir, "script.py")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(code)
                cmd = [sys.executable, file_path]
            elif lang in ("javascript", "js"):
                file_path = os.path.join(sandbox_dir, "script.js")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(code)
                cmd = ["node", file_path]
            elif lang in ("shell", "bash", "sh"):
                file_path = os.path.join(sandbox_dir, "script.bat")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"@echo off\n{code}")
                cmd = ["cmd.exe", "/c", file_path]
            else:
                return ToolResult(success=False, output="", error=f"Unsupported language: {lang}")

            # Scrub environment
            safe_env = {
                k: v for k, v in os.environ.items()
                if not re.search(r"(?i)(key|secret|token|password|auth|cred)", k)
            }

            proc = None
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=sandbox_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=safe_env,
                )
                stdout, stderr = proc.communicate(timeout=timeout)
                stdout = self._scrub_output(stdout)
                stderr = self._scrub_output(stderr)
                if proc.returncode == 0:
                    return ToolResult(
                        success=True,
                        output=stdout.strip(),
                        metadata={"returncode": 0, "sandbox_dir": sandbox_dir},
                    )
                else:
                    return ToolResult(
                        success=False,
                        output=stdout.strip(),
                        error=stderr.strip() or f"Process exited with code {proc.returncode}",
                        metadata={"returncode": proc.returncode},
                    )
            except subprocess.TimeoutExpired:
                if proc:
                    try:
                        if sys.platform == "win32":
                            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                        else:
                            proc.kill()
                        proc.wait(timeout=1.0)
                    except Exception:
                        pass
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Execution timed out after {timeout} seconds.",
                    metadata={"timed_out": True},
                )
            except Exception as e:
                return ToolResult(success=False, output="", error=str(e))


class ReferenceOdooBuilder(BaseTool):
    """Odoo website and eCommerce mock connector."""

    def __init__(self):
        self.products: List[Dict[str, Any]] = []
        self.pages: List[Dict[str, Any]] = []
        self._next_product_id = 1
        self._next_page_id = 1

    @property
    def name(self) -> str:
        return "odoo_builder"

    @property
    def description(self) -> str:
        return "Builds Odoo website pages and manages eCommerce product catalogs."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create_product", "list_products", "create_page", "update_page", "list_pages"],
                },
                "name": {"type": "string"},
                "price": {"type": "number"},
                "sku": {"type": "string"},
                "url": {"type": "string"},
                "content": {"type": "string"},
                "page_id": {"type": "integer"},
            },
            "required": ["action"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action")
        if action == "create_product":
            prod = {
                "id": self._next_product_id,
                "name": kwargs.get("name", "Default Product"),
                "list_price": kwargs.get("price", 19.99),
                "default_code": kwargs.get("sku", f"SKU-{self._next_product_id}"),
            }
            self.products.append(prod)
            self._next_product_id += 1
            return ToolResult(success=True, output=prod)

        elif action == "list_products":
            return ToolResult(success=True, output=self.products)

        elif action == "create_page":
            page = {
                "id": self._next_page_id,
                "name": kwargs.get("name", "Landing Page"),
                "url": kwargs.get("url", f"/page-{self._next_page_id}"),
                "content": kwargs.get("content", "<h1>Welcome</h1>"),
            }
            self.pages.append(page)
            self._next_page_id += 1
            return ToolResult(success=True, output=page)

        elif action == "list_pages":
            return ToolResult(success=True, output=self.pages)

        return ToolResult(success=False, output=None, error=f"Unknown Odoo action: {action}")


class ReferenceSocialMedia(BaseTool):
    """Instagram and YouTube social media skill."""

    def __init__(self):
        self.posts: List[Dict[str, Any]] = []
        self.scripts: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "social_media"

    @property
    def description(self) -> str:
        return "Publishes social posts to Instagram and generates structured 5-part YouTube video scripts."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["generate_youtube_script", "create_instagram_post", "get_analytics"],
                },
                "topic": {"type": "string"},
                "target_duration_seconds": {"type": "integer", "default": 60},
                "caption": {"type": "string"},
                "image_url": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["action"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action")
        if action == "generate_youtube_script":
            topic = kwargs.get("topic", "AI Advancements")
            dur = kwargs.get("target_duration_seconds", 60)
            script = {
                "topic": topic,
                "target_duration": dur,
                "hook": f"Did you know {topic} is transforming everything?",
                "intro": f"Welcome back! Today we break down {topic}.",
                "scenes": [
                    {"scene_num": 1, "visual": "Dynamic graphic", "audio": "Context overview"},
                    {"scene_num": 2, "visual": "Key data chart", "audio": "In-depth analysis"},
                ],
                "spoken_script": f"Here is why {topic} matters in 2026...",
                "call_to_action": "Subscribe for daily AI agent updates!",
                "metadata": {
                    "title": f"The Ultimate Guide to {topic}",
                    "description": f"Learn all about {topic} in under {dur}s.",
                    "tags": [topic.lower(), "ai", "omniagent"],
                },
            }
            self.scripts.append(script)
            return ToolResult(success=True, output=script)

        elif action == "create_instagram_post":
            caption = kwargs.get("caption", "Discover the power of autonomous AI!")
            tags = kwargs.get("hashtags", ["#ai", "#agent", "#omniagent"])
            formatted_caption = f"{caption}\n\n{' '.join(tags)}"
            post = {
                "id": f"ig_post_{len(self.posts) + 1}",
                "caption": formatted_caption,
                "image_url": kwargs.get("image_url", "https://example.com/asset.jpg"),
                "status": "published",
                "timestamp": time.time(),
            }
            self.posts.append(post)
            return ToolResult(success=True, output=post)

        elif action == "get_analytics":
            analytics = {
                "youtube": {"total_views": 15420, "watch_time_hours": 320, "subscribers_gained": 48},
                "instagram": {"impressions": 8900, "reach": 7400, "likes": 650, "saves": 120},
            }
            return ToolResult(success=True, output=analytics)

        return ToolResult(success=False, output=None, error=f"Unknown social action: {action}")


class ReferenceSEOOptimizer(BaseTool):
    """Technical SEO audit, keyword density, and Schema.org JSON-LD generator."""

    @property
    def name(self) -> str:
        return "seo_optimizer"

    @property
    def description(self) -> str:
        return "Performs technical page audits, keyword density analysis, and generates Schema.org JSON-LD."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["audit_page", "keyword_density", "generate_schema_jsonld"],
                },
                "html_content": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "schema_type": {"type": "string", "default": "SoftwareApplication"},
            },
            "required": ["action"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action")
        html = kwargs.get("html_content", "")

        if action == "audit_page":
            # Compute health score 0-100
            score = 100
            issues = []
            has_title = bool(re.search(r"<title>(.*?)</title>", html, re.IGNORECASE))
            has_meta_desc = bool(re.search(r'<meta[^>]+name=["\']description["\']', html, re.IGNORECASE))
            has_h1 = bool(re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE))
            has_jsonld = bool(re.search(r'type=["\']application/ld\+json["\']', html, re.IGNORECASE))

            if not has_title:
                score -= 30
                issues.append("Missing <title> tag")
            if not has_meta_desc:
                score -= 25
                issues.append("Missing meta description")
            if not has_h1:
                score -= 20
                issues.append("Missing <h1> primary heading")
            if not has_jsonld:
                score -= 15
                issues.append("Missing structured Schema.org JSON-LD")

            return ToolResult(
                success=True,
                output={
                    "health_score": max(0, score),
                    "has_title": has_title,
                    "has_meta_desc": has_meta_desc,
                    "has_h1": has_h1,
                    "has_jsonld": has_jsonld,
                    "issues": issues,
                },
            )

        elif action == "keyword_density":
            clean_text = re.sub(r"<[^>]+>", " ", html).lower()
            words = re.findall(r"\b[a-z]{3,}\b", clean_text)
            total_words = len(words) or 1
            kw_list = kwargs.get("keywords", ["ai", "agent", "omniagent"])
            results = {}
            for kw in kw_list:
                kw_lower = kw.lower()
                count = words.count(kw_lower)
                results[kw] = {
                    "count": count,
                    "density_percent": round((count / total_words) * 100, 2),
                }
            return ToolResult(success=True, output={"total_words": total_words, "keywords": results})

        elif action == "generate_schema_jsonld":
            stype = kwargs.get("schema_type", "SoftwareApplication")
            jsonld = {
                "@context": "https://schema.org",
                "@type": stype,
                "name": "OmniAgent",
                "operatingSystem": "All",
                "applicationCategory": "BusinessApplication",
            }
            return ToolResult(success=True, output=jsonld)

        return ToolResult(success=False, output=None, error=f"Unknown SEO action: {action}")


# ==============================================================================
# M4: Workflow DAG Runner Reference Oracle
# ==============================================================================

class ReferenceWorkflowRunner:
    """Compiles and executes visual workflow DAG graphs."""

    @staticmethod
    def validate_workflow(workflow_dict: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        if "schema_version" not in workflow_dict:
            return False, "Missing schema_version."
        if "nodes" not in workflow_dict or not isinstance(workflow_dict["nodes"], list):
            return False, "Workflow must contain a 'nodes' list."
        if "edges" not in workflow_dict or not isinstance(workflow_dict["edges"], list):
            return False, "Workflow must contain an 'edges' list."

        node_ids = {n["id"] for n in workflow_dict["nodes"] if "id" in n}
        for edge in workflow_dict["edges"]:
            if edge.get("source") == edge.get("target"):
                return False, f"Self-loop detected on node '{edge.get('source')}'."
            if edge.get("source") not in node_ids:
                return False, f"Edge source '{edge.get('source')}' does not exist in nodes."
            if edge.get("target") not in node_ids:
                return False, f"Edge target '{edge.get('target')}' does not exist in nodes."

        # Detect cycle in DAG
        adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}
        in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}
        for edge in workflow_dict["edges"]:
            adj[edge["source"]].append(edge["target"])
            in_degree[edge["target"]] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited_count = 0
        while queue:
            curr = queue.pop(0)
            visited_count += 1
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count < len(node_ids):
            return False, "Circular cycle detected in workflow DAG."

        return True, None

    @staticmethod
    def execute_workflow(
        workflow_dict: Dict[str, Any],
        input_payload: Dict[str, Any],
        session_id: str = "test_session",
    ) -> Dict[str, Any]:
        is_valid, err = ReferenceWorkflowRunner.validate_workflow(workflow_dict)
        if not is_valid:
            raise ValueError(f"Invalid workflow graph: {err}")

        # Topologically sort nodes
        node_map = {n["id"]: n for n in workflow_dict["nodes"]}
        node_outputs: Dict[str, Any] = {}
        execution_trace: List[Dict[str, Any]] = []

        # Find trigger node
        for node in workflow_dict["nodes"]:
            nid = node["id"]
            ntype = node.get("type")
            if ntype == "trigger":
                out = input_payload.get("input", node.get("config", {}).get("default_input", "start"))
                node_outputs[nid] = out
                execution_trace.append({"node_id": nid, "status": "COMPLETED", "output": out})
            elif ntype == "llm":
                out = f"Reasoned response for payload: {list(node_outputs.values())}"
                node_outputs[nid] = out
                execution_trace.append({"node_id": nid, "status": "COMPLETED", "output": out})
            elif ntype == "tool":
                out = {"tool_status": "success", "data": "Executed tool logic"}
                node_outputs[nid] = out
                execution_trace.append({"node_id": nid, "status": "COMPLETED", "output": out})
            elif ntype == "action":
                out = {"synthesis": "Final synthesized workflow output", "trace": execution_trace}
                node_outputs[nid] = out
                execution_trace.append({"node_id": nid, "status": "COMPLETED", "output": out})

        return {
            "session_id": session_id,
            "status": "COMPLETED",
            "execution_trace": execution_trace,
            "final_output": list(node_outputs.values())[-1] if node_outputs else None,
        }
