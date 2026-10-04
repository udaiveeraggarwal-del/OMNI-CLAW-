"""
Privacy Network Manager & Security Context for OmniAgent.

Orchestrates the onion circuit lifecycle, embedded loopback SOCKS5 gateway,
fingerprint scrubber, and delivers NetworkSecurityContext to LLM providers
and tool HTTP sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import socket
from typing import Any, Dict, List, Optional
import requests

from omniagent.security.onion import OnionRouter
from omniagent.security.proxy import SOCKS5Server
from omniagent.security.scrubber import FingerprintScrubber, BrowserShield


class PrivacyMode(str, Enum):
    """Operational mode of the privacy network subsystem."""
    DISABLED = "disabled"              # Direct clearnet
    ONION_CIRCUIT = "onion_circuit"    # Native 3-hop onion mesh + SOCKS5 gateway
    TOR_SOCKS = "tor_socks"            # External Tor daemon (e.g. socks5h://127.0.0.1:9050)
    SECURE_TUNNEL = "secure_tunnel"    # E2E AEAD tunnel gateway


@dataclass
class NetworkSecurityContext:
    """
    Security and privacy tunneling context for network requests (M1 <-> M2 contract).
    """
    enabled: bool = False
    proxy_url: Optional[str] = None
    route_dns_remotely: bool = True
    scrub_fingerprints: bool = True
    mode: PrivacyMode = PrivacyMode.ONION_CIRCUIT
    circuit_hop_count: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "proxy_url": self.proxy_url,
            "route_dns_remotely": self.route_dns_remotely,
            "scrub_fingerprints": self.scrub_fingerprints,
            "mode": self.mode.value if isinstance(self.mode, PrivacyMode) else str(self.mode),
            "circuit_hop_count": self.circuit_hop_count,
        }

    def create_session(self) -> requests.Session:
        """
        Creates and configures a requests.Session adhering to this security context.
        """
        session = requests.Session()
        if self.enabled and self.proxy_url:
            session.proxies = {
                "http": self.proxy_url,
                "https": self.proxy_url,
            }

        if self.scrub_fingerprints:
            scrubber = FingerprintScrubber()
            # Wrap default headers
            session.headers.update({
                "User-Agent": scrubber.user_agent,
                "Accept-Language": scrubber.accept_language,
            })

        return session


class PrivacyNetworkManager:
    """
    Central Manager for Privacy & Security Network Protocols in OmniAgent.
    Manages circuit establishment, SOCKS5 server loopback lifecycle,
    and security context propagation.
    """

    def __init__(
        self,
        mode: PrivacyMode = PrivacyMode.ONION_CIRCUIT,
        host: str = "127.0.0.1",
        port: int = 0,
        enable_header_scrubbing: bool = True,
        enable_dns_leak_protection: bool = True,
    ):
        self.mode = mode
        self.host = host
        self.port = port
        self.enable_header_scrubbing = enable_header_scrubbing
        self.enable_dns_leak_protection = enable_dns_leak_protection

        self.onion_router = OnionRouter()
        self.socks_server: Optional[SOCKS5Server] = None
        self.scrubber = FingerprintScrubber()
        self.is_active = False

        self._context = NetworkSecurityContext(
            enabled=False,
            proxy_url=None,
            route_dns_remotely=self.enable_dns_leak_protection,
            scrub_fingerprints=self.enable_header_scrubbing,
            mode=self.mode,
            circuit_hop_count=3,
        )

    def start(self) -> None:
        """
        Start the privacy subsystem:
        1. Establishes the 3-hop onion circuit.
        2. Spawns the embedded SOCKS5 proxy server.
        3. Configures the active NetworkSecurityContext.
        """
        if self.mode == PrivacyMode.DISABLED:
            self._context.enabled = False
            self._context.proxy_url = None
            self.is_active = True
            return

        # 1. Establish Onion Circuit
        if self.mode == PrivacyMode.ONION_CIRCUIT:
            self.onion_router.build_circuit()

        # 2. Launch Loopback SOCKS5 Server
        if self.mode in (PrivacyMode.ONION_CIRCUIT, PrivacyMode.SECURE_TUNNEL):
            self.socks_server = SOCKS5Server(
                host=self.host,
                port=self.port,
                onion_router=self.onion_router,
            )
            self.socks_server.start()
            proxy_url = self.socks_server.proxy_url
        elif self.mode == PrivacyMode.TOR_SOCKS:
            proxy_url = "socks5h://127.0.0.1:9050"
        else:
            proxy_url = None

        # 3. Update security context
        self._context.enabled = True
        self._context.proxy_url = proxy_url
        self._context.mode = self.mode
        self.is_active = True

    def stop(self) -> None:
        """
        Tear down the privacy subsystem cleanly:
        1. Stops SOCKS5 server.
        2. Tears down onion circuits and clears ephemeral keys.
        3. Resets security context.
        """
        if self.socks_server:
            self.socks_server.stop()
            self.socks_server = None

        if self.onion_router:
            self.onion_router.teardown()

        self._context.enabled = False
        self._context.proxy_url = None
        self.is_active = False

    def get_context(self) -> NetworkSecurityContext:
        """Retrieve the active NetworkSecurityContext."""
        return self._context

    def get_proxy_url(self) -> Optional[str]:
        """Retrieve current proxy URL (e.g. 'socks5://127.0.0.1:9055')."""
        return self._context.proxy_url

    def scrub_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Scrub outbound HTTP request headers."""
        return self.scrubber.scrub_headers(headers)

    def get_browser_args(self) -> List[str]:
        """Retrieve stealth launch arguments for Playwright / Chromium."""
        return BrowserShield.get_launch_args(proxy_url=self.get_proxy_url())

    def __enter__(self) -> PrivacyNetworkManager:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
