"""
Unit Tests for OmniAgent Milestone 2: Privacy & Security Network Protocols.

Tests:
1. Cryptographic Primitives (AEAD, X25519 ECDH, HKDF-SHA256, Tamper Detection).
2. Onion Routing Subsystem (512-byte cells, 3-hop telescoping circuit, concentric peeling).
3. SOCKS5 Proxy Gateway (RFC 1928, Zero DNS Leak remote domain resolution, loopback tunneling).
4. Fingerprint Scrubber (Header stripping, User-Agent normalization, BrowserShield).
5. Privacy Network Manager & NetworkSecurityContext lifecycle.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import unittest

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
)
from omniagent.security.proxy import (
    SOCKS5Server,
    DNSGuard,
)
from omniagent.security.scrubber import (
    FingerprintScrubber,
    BrowserShield,
    TOR_BROWSER_USER_AGENT,
    DEFAULT_TRACKING_HEADERS,
)
from omniagent.security.manager import (
    PrivacyNetworkManager,
    NetworkSecurityContext,
    PrivacyMode,
)


class TestCryptoPrimitives(unittest.TestCase):
    """Tests for AEAD encryption, X25519 key exchange, and HKDF key derivation."""

    def test_x25519_keypair_generation(self):
        """Test ephemeral keypair generation produces valid 32-byte keys."""
        priv, pub = X25519KeyExchange.generate_keypair()
        self.assertEqual(len(priv), 32)
        self.assertEqual(len(pub), 32)
        self.assertNotEqual(priv, pub)

    def test_x25519_ecdh_shared_secret(self):
        """Test Diffie-Hellman negotiation between two parties produces identical shared secret."""
        priv_a, pub_a = X25519KeyExchange.generate_keypair()
        priv_b, pub_b = X25519KeyExchange.generate_keypair()

        shared_ab = X25519KeyExchange.compute_shared_secret(priv_a, pub_b)
        shared_ba = X25519KeyExchange.compute_shared_secret(priv_b, pub_a)

        self.assertEqual(shared_ab, shared_ba)
        self.assertEqual(len(shared_ab), 32)

    def test_x25519_rfc7748_official_vectors(self):
        """Verify X25519 against official RFC 7748 Section 5 test vectors."""
        # Alice
        alice_priv = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        expected_alice_pub = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        # Bob
        bob_priv = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
        expected_bob_pub = bytes.fromhex("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
        # Shared secret
        expected_shared = bytes.fromhex("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")

        shared_a = x25519_diffie_hellman(alice_priv, expected_bob_pub)
        shared_b = x25519_diffie_hellman(bob_priv, expected_alice_pub)

        self.assertEqual(shared_a, expected_shared)
        self.assertEqual(shared_b, expected_shared)

    def test_hkdf_sha256_rfc5869_derivation(self):
        """Test RFC 5869 HKDF extraction and expansion."""
        ikm = b"secret-key-material-for-hkdf-test"
        salt = b"test-salt-string"
        info = b"omniagent-circuit-v1"

        derived_32 = hkdf_sha256(ikm, salt, info, length=32)
        derived_64 = hkdf_sha256(ikm, salt, info, length=64)

        self.assertEqual(len(derived_32), 32)
        self.assertEqual(len(derived_64), 64)
        self.assertEqual(derived_64[:32], derived_32)

    def test_hkdf_hop_keys_derivation(self):
        """Test circuit hop key derivation produces required key and digest lengths."""
        shared_secret = os.urandom(32)
        keys = HKDF.derive_hop_keys(shared_secret, context="test-hop")
        self.assertIn("k_fwd", keys)
        self.assertIn("k_bwd", keys)
        self.assertIn("d_fwd", keys)
        self.assertIn("d_bwd", keys)
        self.assertEqual(len(keys["k_fwd"]), 32)
        self.assertEqual(len(keys["k_bwd"]), 32)
        self.assertEqual(len(keys["d_fwd"]), 20)
        self.assertEqual(len(keys["d_bwd"]), 20)

    def test_chacha20_poly1305_rfc8439_official_vector(self):
        """Verify pure Python ChaCha20-Poly1305 matches official RFC 8439 Section 2.8.2 vector."""
        key = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = bytes.fromhex("070000004041424344454647")
        aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        pt = b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it."
        expected_tag = bytes.fromhex("1ae10b594f09e26a7e902ecbd0600691")

        ct_and_tag = ChaCha20Poly1305Cipher.encrypt(key, nonce, pt, aad)
        tag = ct_and_tag[-16:]
        self.assertEqual(tag, expected_tag)

        recovered = ChaCha20Poly1305Cipher.decrypt(key, nonce, ct_and_tag, aad)
        self.assertEqual(recovered, pt)

    def test_chacha20_poly1305_roundtrip(self):
        """Test symmetric roundtrip for arbitrary plaintexts with AAD."""
        key = os.urandom(32)
        nonce = os.urandom(12)
        pt = b"Confidential autonomous agent plan payload"
        aad = b"header-aad-metadata"

        enc = ChaCha20Poly1305Cipher.encrypt(key, nonce, pt, aad)
        dec = ChaCha20Poly1305Cipher.decrypt(key, nonce, enc, aad)
        self.assertEqual(dec, pt)

    def test_aes256_gcm_roundtrip(self):
        """Test AES-256-GCM encryption/decryption roundtrip."""
        key = os.urandom(32)
        nonce = os.urandom(12)
        pt = b"Payload secured via AES-256-GCM AEAD"
        aad = b"associated-data"

        enc = AES256GCMCipher.encrypt(key, nonce, pt, aad)
        dec = AES256GCMCipher.decrypt(key, nonce, enc, aad)
        self.assertEqual(dec, pt)

    def test_aead_encrypt_decrypt_dict_format(self):
        """Test aead_encrypt and aead_decrypt using base64 dictionary format."""
        key = os.urandom(32)
        pt = b"Sensitive model query instructions."
        enc_dict = aead_encrypt(key, pt, cipher_type="chacha20-poly1305")
        self.assertIn("nonce", enc_dict)
        self.assertIn("ciphertext", enc_dict)
        self.assertIn("tag", enc_dict)

        dec = aead_decrypt(key, enc_dict, cipher_type="chacha20-poly1305")
        self.assertEqual(dec, pt)

    def test_aead_tamper_detection_ciphertext(self):
        """Test that altering 1 bit of ciphertext raises AuthenticationError."""
        key = os.urandom(32)
        pt = b"Critical security policy"
        enc = aead_encrypt(key, pt)

        tampered = dict(enc)
        raw_ct = bytearray(base64.b64decode(tampered["ciphertext"]))
        raw_ct[0] ^= 0x01
        tampered["ciphertext"] = base64.b64encode(raw_ct).decode("utf-8")

        with self.assertRaises((AuthenticationError, ValueError)):
            aead_decrypt(key, tampered)

    def test_aead_tamper_detection_tag(self):
        """Test that altering 1 bit of tag raises AuthenticationError."""
        key = os.urandom(32)
        pt = b"Security token payload"
        enc = aead_encrypt(key, pt)

        tampered = dict(enc)
        raw_tag = bytearray(base64.b64decode(tampered["tag"]))
        raw_tag[0] ^= 0x01
        tampered["tag"] = base64.b64encode(raw_tag).decode("utf-8")

        with self.assertRaises((AuthenticationError, ValueError)):
            aead_decrypt(key, tampered)

    def test_aead_tamper_detection_aad(self):
        """Test that modifying associated authenticated data raises AuthenticationError."""
        key = os.urandom(32)
        pt = b"Authenticated transaction"
        enc = aead_encrypt(key, pt, aad=b"legit-aad")

        with self.assertRaises((AuthenticationError, ValueError)):
            aead_decrypt(key, enc, aad=b"tampered-aad")

    def test_binary_payload_pack_unpack(self):
        """Test binary packed helper encrypt_payload and decrypt_payload."""
        key = os.urandom(32)
        pt = b"Binary stream data across Tor circuit"
        packed = encrypt_payload(key, pt, aad=b"session-1")
        self.assertGreater(len(packed), len(pt))

        dec = decrypt_payload(key, packed, aad=b"session-1")
        self.assertEqual(dec, pt)


class TestOnionRouting(unittest.TestCase):
    """Tests for 512-byte cell protocol and 3-hop telescoping circuit routing."""

    def test_onion_cell_pack_unpack_exact_512_bytes(self):
        """Test cell packing produces exactly 512 bytes and unpacking recovers original fields."""
        cell = OnionCell(command=CellCommand.CREATE2, circuit_id=2001, payload=b"ephemeral-key-payload")
        packed = cell.pack()
        self.assertEqual(len(packed), CELL_SIZE)

        unpacked = OnionCell.unpack(packed)
        self.assertEqual(unpacked.command, "CREATE2")
        self.assertEqual(unpacked.circuit_id, 2001)
        self.assertEqual(unpacked.payload, b"ephemeral-key-payload")

    def test_onion_cell_oversized_payload_rejected(self):
        """Test cell pack rejects payload exceeding 500 bytes (header is 12 bytes)."""
        big_payload = b"X" * 501
        cell = OnionCell(command="RELAY_DATA", circuit_id=1, payload=big_payload)
        with self.assertRaises(ValueError):
            cell.pack()

    def test_onion_cell_various_commands(self):
        """Test cell pack/unpack across different command types."""
        commands = [
            CellCommand.CREATE2,
            CellCommand.CREATED2,
            CellCommand.EXTEND2,
            CellCommand.EXTENDED2,
            CellCommand.RELAY_DATA,
            CellCommand.DESTROY,
            CellCommand.PADDING,
        ]
        for cmd in commands:
            cell = OnionCell(command=cmd, circuit_id=123, payload=b"test")
            unpacked = OnionCell.unpack(cell.pack())
            self.assertEqual(unpacked.command, cmd.value)

    def test_3hop_circuit_building(self):
        """Test 3-hop telescoping circuit negotiation establishes 3 hops with distinct keys."""
        router = OnionRouter()
        built = router.build_circuit()
        self.assertTrue(built)
        self.assertTrue(router.established)

        # Assert 3 distinct hop keys
        self.assertEqual(len(router.entry_key), 32)
        self.assertEqual(len(router.middle_key), 32)
        self.assertEqual(len(router.exit_key), 32)
        self.assertNotEqual(router.entry_key, router.middle_key)
        self.assertNotEqual(router.middle_key, router.exit_key)

    def test_concentric_onion_layered_encryption_and_peeling(self):
        """
        Test concentric 3-layer onion encryption and sequential peeling:
        Client encrypts: (Exit -> Middle -> Entry)
        Entry peels Hop 1 -> Middle peels Hop 2 -> Exit peels Hop 3 to recover plaintext.
        """
        router = OnionRouter()
        router.build_circuit()

        original_payload = b"GET /v1/chat/completions HTTP/1.1\r\nHost: api.openai.com\r\n\r\n"
        onion_package = router.onion_encrypt(original_payload)

        # Intermediate representations must not reveal cleartext
        self.assertNotIn(original_payload, onion_package)

        # Hop 1 (Entry Guard peels outer layer)
        peeled_1 = router.peel_entry(onion_package)
        self.assertNotIn(original_payload, peeled_1)

        # Hop 2 (Middle Relay peels middle layer)
        peeled_2 = router.peel_middle(peeled_1)
        self.assertNotIn(original_payload, peeled_2)

        # Hop 3 (Exit Node peels innermost layer)
        peeled_3 = router.peel_exit(peeled_2)
        self.assertEqual(peeled_3, original_payload)

    def test_reverse_response_layered_encryption_and_peeling(self):
        """Test server response reverse onion encryption from Exit -> Middle -> Entry."""
        router = OnionRouter()
        router.build_circuit()

        server_response = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\"}"
        reverse_encrypted = router.onion_encrypt_response(server_response)
        self.assertNotIn(server_response, reverse_encrypted)

        recovered = router.client_peel_response(reverse_encrypted)
        self.assertEqual(recovered, server_response)

    def test_onion_mesh_directory(self):
        """Test OnionMesh directory and path selection."""
        mesh = OnionMesh()
        entry, middle, exit_node = mesh.select_path()
        self.assertEqual(entry.role, RelayRole.ENTRY)
        self.assertEqual(middle.role, RelayRole.MIDDLE)
        self.assertEqual(exit_node.role, RelayRole.EXIT)
        self.assertNotEqual(entry.node_id, middle.node_id)
        self.assertNotEqual(middle.node_id, exit_node.node_id)

    def test_circuit_teardown(self):
        """Test circuit teardown clears state and invalidates circuit."""
        router = OnionRouter()
        router.build_circuit()
        self.assertTrue(router.established)

        router.teardown()
        self.assertFalse(router.established)
        with self.assertRaises(RuntimeError):
            router.onion_encrypt(b"data after teardown")


class TestSOCKS5Proxy(unittest.TestCase):
    """Tests for loopback SOCKS5 gateway and zero-DNS-leak remote domain resolution."""

    def test_socks5_server_lifecycle(self):
        """Test starting and stopping SOCKS5 proxy on loopback."""
        proxy = SOCKS5Server(host="127.0.0.1", port=0)
        self.assertFalse(proxy.is_running)

        proxy.start()
        self.assertTrue(proxy.is_running)
        self.assertGreater(proxy.port, 0)
        self.assertTrue(proxy.proxy_url.startswith("socks5://127.0.0.1:"))

        proxy.stop()
        self.assertFalse(proxy.is_running)

    def test_socks5_handshake_no_auth(self):
        """Test SOCKS5 method handshake (RFC 1928: 0x05 0x01 0x00 -> 0x05 0x00)."""
        proxy = SOCKS5Server(host="127.0.0.1", port=0)
        proxy.start()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", proxy.port))
            # Send greeting
            sock.sendall(b"\x05\x01\x00")
            reply = sock.recv(2)
            self.assertEqual(reply, b"\x05\x00")
            sock.close()
        finally:
            proxy.stop()

    def test_socks5_remote_domain_atyp03_zero_dns_leak(self):
        """
        Verify that remote domain requests (ATYP=0x03) are parsed directly
        and registered in DNSGuard with zero local DNS resolution.
        """
        proxy = SOCKS5Server(host="127.0.0.1", port=0)
        proxy.start()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", proxy.port))
            sock.sendall(b"\x05\x01\x00")
            self.assertEqual(sock.recv(2), b"\x05\x00")

            # Connect request to domain 'target-api.tor.internal' on port 443
            domain = b"target-api.tor.internal"
            connect_req = (
                b"\x05\x01\x00\x03"
                + bytes([len(domain)])
                + domain
                + (443).to_bytes(2, "big")
            )
            sock.sendall(connect_req)
            reply = sock.recv(10)
            self.assertEqual(reply[:2], b"\x05\x00")  # Succeeded

            self.assertEqual(proxy.last_target_host, "target-api.tor.internal")
            self.assertEqual(proxy.last_target_port, 443)
            self.assertEqual(proxy.last_atyp, 0x03)
            self.assertIn("target-api.tor.internal", proxy.dns_guard.intercepted_domains)
            self.assertGreaterEqual(proxy.dns_guard.lookups_prevented_count, 1)

            sock.close()
        finally:
            proxy.stop()

    def test_socks5_ipv4_connection(self):
        """Test SOCKS5 connection with IPv4 address (ATYP=0x01)."""
        proxy = SOCKS5Server(host="127.0.0.1", port=0)
        proxy.start()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", proxy.port))
            sock.sendall(b"\x05\x01\x00")
            self.assertEqual(sock.recv(2), b"\x05\x00")

            # Connect to 192.168.1.100:8080
            ip_bytes = socket.inet_aton("192.168.1.100")
            req = b"\x05\x01\x00\x01" + ip_bytes + (8080).to_bytes(2, "big")
            sock.sendall(req)
            reply = sock.recv(10)
            self.assertEqual(reply[:2], b"\x05\x00")
            self.assertEqual(proxy.last_target_host, "192.168.1.100")
            self.assertEqual(proxy.last_target_port, 8080)
            sock.close()
        finally:
            proxy.stop()

    def test_socks5_bidirectional_data_tunnel(self):
        """Test sending HTTP data through SOCKS5 proxy and receiving tunneled response."""
        proxy = SOCKS5Server(host="127.0.0.1", port=0)
        proxy.start()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", proxy.port))
            sock.sendall(b"\x05\x01\x00")
            sock.recv(2)

            domain = b"api.anthropic.com"
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + (443).to_bytes(2, "big"))
            sock.recv(10)

            # Send HTTP request
            sock.sendall(b"GET /v1/health HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n")
            data = sock.recv(1024)
            self.assertIn(b"OmniAgent Proxy OK", data)
            sock.close()
        finally:
            proxy.stop()

    def test_socks5_context_manager(self):
        """Test using SOCKS5Server as context manager."""
        with SOCKS5Server(host="127.0.0.1", port=0) as srv:
            self.assertTrue(srv.is_running)
            port = srv.port
        self.assertFalse(srv.is_running)


class TestFingerprintScrubber(unittest.TestCase):
    """Tests for HTTP header sanitization and Playwright anti-fingerprinting."""

    def test_scrub_tracking_headers_removal(self):
        """Test removal of tracking and IP leak headers."""
        raw_headers = {
            "Host": "api.openai.com",
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-test",
            "X-Forwarded-For": "203.0.113.195",
            "X-Forwarded-Host": "client.internal",
            "X-Forwarded-Proto": "https",
            "Via": "1.1 proxy.internal",
            "Client-IP": "203.0.113.195",
            "X-Real-IP": "203.0.113.195",
            "CF-Connecting-IP": "203.0.113.195",
            "Sec-CH-UA": '"Chromium";v="128"',
            "Sec-CH-UA-Platform": '"Windows"',
            "X-Request-Id": "req-987654321",
        }
        scrubbed = FingerprintScrubber.scrub(raw_headers)

        # Tracking headers must be stripped
        for h in [
            "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
            "Via", "Client-IP", "X-Real-IP", "CF-Connecting-IP",
            "Sec-CH-UA", "Sec-CH-UA-Platform", "X-Request-Id"
        ]:
            self.assertNotIn(h, scrubbed)

        # Essential headers must be preserved
        self.assertEqual(scrubbed["Host"], "api.openai.com")
        self.assertEqual(scrubbed["Content-Type"], "application/json")
        self.assertEqual(scrubbed["Authorization"], "Bearer sk-test")

        # Identity headers normalized
        self.assertEqual(scrubbed["User-Agent"], TOR_BROWSER_USER_AGENT)
        self.assertEqual(scrubbed["Accept-Language"], "en-US,en;q=0.5")

    def test_case_insensitive_header_scrubbing(self):
        """Test that header scrubbing works case-insensitively."""
        scrubber = FingerprintScrubber()
        headers = {
            "x-forwarded-for": "1.2.3.4",
            "X-REAL-IP": "5.6.7.8",
            "vIa": "proxy",
            "SEC-ch-ua-mobile": "?0",
        }
        scrubbed = scrubber.scrub_headers(headers)
        self.assertEqual(len([k for k in scrubbed if k.lower() in DEFAULT_TRACKING_HEADERS]), 0)

    def test_browser_shield_launch_args(self):
        """Test BrowserShield generates required Playwright/Chromium flags."""
        args = BrowserShield.get_launch_args(proxy_url="socks5://127.0.0.1:9055")
        self.assertIn("--disable-webrtc", args)
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", args)
        self.assertIn("--timezone=UTC", args)
        self.assertIn("--lang=en-US", args)
        self.assertIn("--proxy-server=socks5://127.0.0.1:9055", args)
        self.assertTrue(any(a.startswith("--user-agent=") for a in args))

    def test_browser_shield_stealth_scripts(self):
        """Test BrowserShield stealth scripts contain canvas and webrtc overrides."""
        scripts = BrowserShield.get_stealth_scripts()
        self.assertGreaterEqual(len(scripts), 2)
        combined = " ".join(scripts)
        self.assertIn("webdriver", combined)
        self.assertIn("HTMLCanvasElement", combined)
        self.assertIn("RTCPeerConnection", combined)


class TestPrivacyNetworkManager(unittest.TestCase):
    """Tests for PrivacyNetworkManager orchestrating circuits and proxies."""

    def test_manager_lifecycle_onion_mode(self):
        """Test PrivacyNetworkManager in ONION_CIRCUIT mode."""
        mgr = PrivacyNetworkManager(mode=PrivacyMode.ONION_CIRCUIT, host="127.0.0.1", port=0)
        self.assertFalse(mgr.is_active)

        mgr.start()
        self.assertTrue(mgr.is_active)
        ctx = mgr.get_context()
        self.assertTrue(ctx.enabled)
        self.assertIsNotNone(ctx.proxy_url)
        self.assertTrue(ctx.proxy_url.startswith("socks5://127.0.0.1:"))
        self.assertEqual(ctx.circuit_hop_count, 3)

        mgr.stop()
        self.assertFalse(mgr.is_active)
        self.assertFalse(mgr.get_context().enabled)

    def test_manager_disabled_mode(self):
        """Test PrivacyNetworkManager in DISABLED mode (direct clearnet)."""
        mgr = PrivacyNetworkManager(mode=PrivacyMode.DISABLED)
        mgr.start()
        ctx = mgr.get_context()
        self.assertFalse(ctx.enabled)
        self.assertIsNone(ctx.proxy_url)
        mgr.stop()

    def test_manager_tor_socks_mode(self):
        """Test PrivacyNetworkManager in TOR_SOCKS mode."""
        mgr = PrivacyNetworkManager(mode=PrivacyMode.TOR_SOCKS)
        mgr.start()
        ctx = mgr.get_context()
        self.assertTrue(ctx.enabled)
        self.assertEqual(ctx.proxy_url, "socks5h://127.0.0.1:9050")
        mgr.stop()

    def test_network_security_context_create_session(self):
        """Test NetworkSecurityContext.create_session configures requests.Session properly."""
        ctx = NetworkSecurityContext(
            enabled=True,
            proxy_url="socks5://127.0.0.1:9055",
            scrub_fingerprints=True,
        )
        session = ctx.create_session()
        self.assertEqual(session.proxies.get("http"), "socks5://127.0.0.1:9055")
        self.assertEqual(session.proxies.get("https"), "socks5://127.0.0.1:9055")
        self.assertEqual(session.headers.get("User-Agent"), TOR_BROWSER_USER_AGENT)

    def test_manager_context_manager(self):
        """Test PrivacyNetworkManager with context manager syntax."""
        with PrivacyNetworkManager(mode=PrivacyMode.ONION_CIRCUIT, port=0) as mgr:
            self.assertTrue(mgr.is_active)
            self.assertIsNotNone(mgr.get_proxy_url())
        self.assertFalse(mgr.is_active)


if __name__ == "__main__":
    unittest.main()
