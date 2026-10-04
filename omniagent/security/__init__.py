"""
OmniAgent Security & Privacy Network Protocols Subsystem.

Provides:
- Cryptographic Primitives (AEAD AES-256-GCM / ChaCha20-Poly1305, ECDH X25519, HKDF-SHA256).
- Tor-Inspired 3-Hop Onion Routing with 512-byte fixed-size cells.
- Loopback SOCKS5 Proxy Gateway with Zero DNS Leakage.
- Fingerprint & Header Scrubber and BrowserShield.
- PrivacyNetworkManager and NetworkSecurityContext.
"""

from __future__ import annotations

from omniagent.security.crypto import (
    AuthenticationError,
    X25519KeyExchange,
    HKDF,
    ChaCha20Poly1305Cipher,
    AES256GCMCipher,
    aead_encrypt,
    aead_decrypt,
    encrypt_payload,
    decrypt_payload,
    hkdf_sha256,
    x25519_keypair,
    x25519_diffie_hellman,
)

from omniagent.security.onion import (
    CELL_SIZE,
    CellCommand,
    OnionCell,
    OnionRouter,
    OnionCircuit,
    OnionMesh,
    RelayNode,
    RelayRole,
    CircuitHop,
)

from omniagent.security.proxy import (
    SOCKS5Server,
    DNSGuard,
)

from omniagent.security.scrubber import (
    FingerprintScrubber,
    BrowserShield,
    TOR_BROWSER_USER_AGENT,
    CHROME_NORMALIZED_USER_AGENT,
    DEFAULT_TRACKING_HEADERS,
)

from omniagent.security.manager import (
    PrivacyNetworkManager,
    NetworkSecurityContext,
    PrivacyMode,
)

__all__ = [
    # Crypto
    "AuthenticationError",
    "X25519KeyExchange",
    "HKDF",
    "ChaCha20Poly1305Cipher",
    "AES256GCMCipher",
    "aead_encrypt",
    "aead_decrypt",
    "encrypt_payload",
    "decrypt_payload",
    "hkdf_sha256",
    "x25519_keypair",
    "x25519_diffie_hellman",
    # Onion
    "CELL_SIZE",
    "CellCommand",
    "OnionCell",
    "OnionRouter",
    "OnionCircuit",
    "OnionMesh",
    "RelayNode",
    "RelayRole",
    "CircuitHop",
    # Proxy
    "SOCKS5Server",
    "DNSGuard",
    # Scrubber
    "FingerprintScrubber",
    "BrowserShield",
    "TOR_BROWSER_USER_AGENT",
    "CHROME_NORMALIZED_USER_AGENT",
    "DEFAULT_TRACKING_HEADERS",
    # Manager
    "PrivacyNetworkManager",
    "NetworkSecurityContext",
    "PrivacyMode",
]
