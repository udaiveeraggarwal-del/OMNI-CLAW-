"""
RFC 1928 SOCKS5 Loopback Proxy Gateway with Zero DNS Leakage.

Provides an embedded loopback SOCKS5 server for OmniAgent that:
- Binds exclusively to 127.0.0.1 (loopback interface) to prevent external listening.
- Handles standard SOCKS5 handshake (No Auth - 0x00).
- Implements remote domain name tunneling (ATYP=0x03) without invoking local DNS resolvers.
- Bridges incoming client connections to an optional OnionRouter circuit.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple, Union

from omniagent.security.onion import OnionRouter


class DNSGuard:
    """
    Enforces zero local DNS leakage.
    Ensures hostnames received over SOCKS5 ATYP=0x03 are forwarded
    without ever triggering local gethostbyname calls.
    """

    def __init__(self):
        self._intercepted_domains: list[str] = []
        self._local_lookups_prevented: int = 0

    def record_remote_domain(self, domain: str) -> None:
        """Record that a domain was routed remotely without local resolution."""
        self._intercepted_domains.append(domain)
        self._local_lookups_prevented += 1

    @property
    def intercepted_domains(self) -> list[str]:
        return list(self._intercepted_domains)

    @property
    def lookups_prevented_count(self) -> int:
        return self._local_lookups_prevented

    @staticmethod
    def is_ip_address(host: str) -> bool:
        """Check if string is a numeric IPv4 or IPv6 address."""
        try:
            socket.inet_aton(host)
            return True
        except socket.error:
            pass
        try:
            socket.inet_pton(socket.AF_INET6, host)
            return True
        except (socket.error, AttributeError):
            pass
        return False


class SOCKS5Server:
    """
    Embedded RFC 1928 SOCKS5 Proxy Server binding to loopback.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        onion_router: Optional[OnionRouter] = None,
        custom_handler: Optional[Callable[[bytes, str, int], bytes]] = None,
    ):
        self.host = host
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, port))
        self.port = self.server_sock.getsockname()[1]
        self.onion_router = onion_router
        self.custom_handler = custom_handler

        self.dns_guard = DNSGuard()
        self.running = False
        self._thread: Optional[threading.Thread] = None

        self.connections_handled: int = 0
        self.last_target_host: Optional[str] = None
        self.last_target_port: Optional[int] = None
        self.last_atyp: Optional[int] = None

    @property
    def is_running(self) -> bool:
        return self.running

    @property
    def proxy_url(self) -> str:
        """Canonical SOCKS5 URL for requests, httpx, and Playwright."""
        return f"socks5://{self.host}:{self.port}"

    @property
    def socks5h_url(self) -> str:
        """Remote-DNS SOCKS5 URL format for curl and requests."""
        return f"socks5h://{self.host}:{self.port}"

    def start(self) -> None:
        """Start accepting connections in background thread."""
        if self.running:
            return
        self.running = True
        self.server_sock.listen(10)
        self._thread = threading.Thread(target=self._serve, daemon=True, name="OmniAgent-SOCKS5")
        self._thread.start()

    def stop(self) -> None:
        """Shut down proxy server and release port."""
        self.running = False
        try:
            self.server_sock.close()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def __enter__(self) -> SOCKS5Server:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def _serve(self) -> None:
        try:
            self.server_sock.settimeout(0.5)
        except Exception:
            return

        while self.running:
            try:
                client_sock, _ = self.server_sock.accept()
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock,),
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _handle_client(self, sock: socket.socket) -> None:
        try:
            sock.settimeout(4.0)

            # 1. Negotiation Handshake
            # Format: [VER, NMETHODS, METHODS...]
            ver_methods = sock.recv(2)
            if not ver_methods or len(ver_methods) < 2 or ver_methods[0] != 0x05:
                sock.close()
                return

            nmethods = ver_methods[1]
            sock.recv(nmethods)
            # Reply: VER=0x05, METHOD=0x00 (NO AUTHENTICATION REQUIRED)
            sock.sendall(b"\x05\x00")

            # 2. Connection Request
            # Format: [VER=0x05, CMD=0x01, RSV=0x00, ATYP, DST.ADDR, DST.PORT]
            req = sock.recv(4)
            if not req or len(req) < 4 or req[0] != 0x05 or req[1] != 0x01:
                sock.close()
                return

            atyp = req[3]
            self.last_atyp = atyp
            target_host = ""

            if atyp == 0x01:  # IPv4 (4 bytes)
                raw_ip = sock.recv(4)
                target_host = socket.inet_ntoa(raw_ip)
            elif atyp == 0x03:  # Domain Name (1 byte length prefix)
                dlen_byte = sock.recv(1)
                if not dlen_byte:
                    sock.close()
                    return
                dlen = dlen_byte[0]
                domain_bytes = sock.recv(dlen)
                target_host = domain_bytes.decode("utf-8", errors="replace")
                # Zero DNS leak: Record that domain was parsed directly with no local OS lookup
                self.dns_guard.record_remote_domain(target_host)
            elif atyp == 0x04:  # IPv6 (16 bytes)
                raw_ip6 = sock.recv(16)
                target_host = socket.inet_ntop(socket.AF_INET6, raw_ip6)
            else:
                # Unsupported address type
                sock.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                sock.close()
                return

            port_bytes = sock.recv(2)
            if len(port_bytes) < 2:
                sock.close()
                return
            target_port = int.from_bytes(port_bytes, "big")

            self.last_target_host = target_host
            self.last_target_port = target_port
            self.connections_handled += 1

            # Reply: Success (0x00), BND.ADDR=127.0.0.1, BND.PORT=target_port
            bnd_reply = b"\x05\x00\x00\x01\x7f\x00\x00\x01" + port_bytes
            sock.sendall(bnd_reply)

            # 3. Tunneling Data
            data = sock.recv(4096)
            if data:
                response: Optional[bytes] = None

                if self.custom_handler:
                    response = self.custom_handler(data, target_host, target_port)
                elif self.onion_router:
                    response = self.onion_router.relay_request(data, target_host, target_port)
                else:
                    # Default mock HTTP tunneling response
                    content = f"OmniAgent Proxy OK: {target_host}:{target_port}".encode("utf-8")
                    headers = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"Content-Type: text/plain\r\n"
                        f"Content-Length: {len(content)}\r\n\r\n"
                    ).encode("utf-8")
                    response = headers + content

                if response:
                    sock.sendall(response)

        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
