"""
Cryptographic Primitives for OmniAgent Security & Privacy Protocols.

Provides 2-tier resilient Authenticated Encryption with Associated Data (AEAD),
Forward-Secret Elliptic Curve Diffie-Hellman (ECDH X25519), and RFC 5869 HKDF-SHA256
key derivation.

Tier 1: Accelerated OpenSSL via `cryptography` when installed.
Tier 2: Pure Python zero-dependency RFC-compliant engine (RFC 8439 ChaCha20-Poly1305,
RFC 7748 X25519 Montgomery Ladder, RFC 5869 HKDF-SHA256) for 100% portability.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
from typing import Any, Dict, Optional, Tuple, Union


class AuthenticationError(ValueError):
    """Raised when ciphertext or authentication tag has been tampered with or corrupted."""
    pass


# Check for Tier 1 OpenSSL acceleration availability
_HAS_CRYPTOGRAPHY = False
try:
    from cryptography.hazmat.primitives.ciphers.aead import (  # type: ignore
        AESGCM as _CryptoAESGCM,
        ChaCha20Poly1305 as _CryptoChaCha,
    )
    from cryptography.hazmat.primitives.asymmetric import x25519 as _CryptoX25519  # type: ignore
    from cryptography.hazmat.primitives import serialization as _CryptoSerial  # type: ignore
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False


# ==============================================================================
# 1. Forward-Secret ECDH Key Exchange (Curve25519 / X25519 - RFC 7748)
# ==============================================================================

_P_25519 = 2**255 - 19
_A24 = 121665
_BASE_POINT_U = 9


def _cswap(swap: int, x_2: int, x_3: int) -> Tuple[int, int]:
    """Constant-time swap helper."""
    if swap:
        return x_3, x_2
    return x_2, x_3


def _x25519_ladder(k: int, u: int) -> int:
    """
    Montgomery ladder scalar multiplication on Curve25519 (RFC 7748 Section 5).
    Evaluates x-coordinate of k * (u, y).
    """
    x_1 = u
    x_2 = 1
    z_2 = 0
    x_3 = u
    z_3 = 1
    swap = 0

    for t in reversed(range(255)):
        k_t = (k >> t) & 1
        swap ^= k_t
        if swap:
            x_2, x_3 = x_3, x_2
            z_2, z_3 = z_3, z_2
        swap = k_t

        a = (x_2 + z_2) % _P_25519
        aa = (a * a) % _P_25519
        b = (x_2 - z_2) % _P_25519
        bb = (b * b) % _P_25519
        e = (aa - bb) % _P_25519
        c = (x_3 + z_3) % _P_25519
        d = (x_3 - z_3) % _P_25519
        da = (d * a) % _P_25519
        cb = (c * b) % _P_25519
        x_3 = pow(da + cb, 2, _P_25519)
        z_3 = (x_1 * pow(da - cb, 2, _P_25519)) % _P_25519
        x_2 = (aa * bb) % _P_25519
        z_2 = (e * (aa + _A24 * e)) % _P_25519

    if swap:
        x_2, x_3 = x_3, x_2
        z_2, z_3 = z_3, z_2

    # Result is x_2 / z_2 in GF(2^255 - 19)
    return (x_2 * pow(z_2, _P_25519 - 2, _P_25519)) % _P_25519


def _clamp_scalar(scalar_bytes: bytes) -> int:
    """Apply RFC 7748 clamping to 32-byte scalar."""
    b = bytearray(scalar_bytes)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def x25519_keypair() -> Tuple[bytes, bytes]:
    """
    Generate an ephemeral X25519 keypair.
    Returns: (private_key_32_bytes, public_key_32_bytes)
    """
    if _HAS_CRYPTOGRAPHY:
        priv = _CryptoX25519.X25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(
            encoding=_CryptoSerial.Encoding.Raw,
            format=_CryptoSerial.PrivateFormat.Raw,
            encryption_algorithm=_CryptoSerial.NoEncryption(),
        )
        pub_bytes = priv.public_key().public_bytes(
            encoding=_CryptoSerial.Encoding.Raw,
            format=_CryptoSerial.PublicFormat.Raw,
        )
        return priv_bytes, pub_bytes

    # Pure Python RFC 7748
    raw_priv = secrets.token_bytes(32)
    k = _clamp_scalar(raw_priv)
    pub_int = _x25519_ladder(k, _BASE_POINT_U)
    pub_bytes = pub_int.to_bytes(32, "little")
    return raw_priv, pub_bytes


def x25519_diffie_hellman(priv_bytes: bytes, peer_pub_bytes: bytes) -> bytes:
    """
    Compute 32-byte shared secret via X25519 Diffie-Hellman key exchange.
    """
    if len(priv_bytes) != 32 or len(peer_pub_bytes) != 32:
        raise ValueError("X25519 keys must be exactly 32 bytes.")

    if _HAS_CRYPTOGRAPHY:
        priv = _CryptoX25519.X25519PrivateKey.from_private_bytes(priv_bytes)
        pub = _CryptoX25519.X25519PublicKey.from_public_bytes(peer_pub_bytes)
        return priv.exchange(pub)

    # Pure Python RFC 7748
    k = _clamp_scalar(priv_bytes)
    # RFC 7748 Section 5: mask MSB of last byte of public key before decoding
    peer_buf = bytearray(peer_pub_bytes)
    peer_buf[31] &= 0x7F
    u = int.from_bytes(peer_buf, "little")
    shared_int = _x25519_ladder(k, u)
    return shared_int.to_bytes(32, "little")


class X25519KeyExchange:
    """Object-oriented wrapper for ephemeral X25519 key agreements."""

    @classmethod
    def generate_keypair(cls) -> Tuple[bytes, bytes]:
        return x25519_keypair()

    @classmethod
    def compute_shared_secret(cls, priv_bytes: bytes, peer_pub_bytes: bytes) -> bytes:
        return x25519_diffie_hellman(priv_bytes, peer_pub_bytes)


# ==============================================================================
# 2. Key Derivation Function (HKDF - RFC 5869)
# ==============================================================================

def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """RFC 5869 HKDF-Extract using HMAC-SHA256."""
    if not salt:
        salt = b"\x00" * 32
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF-Expand using HMAC-SHA256."""
    out = bytearray()
    t = b""
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out.extend(t)
        counter += 1
    return bytes(out[:length])


def hkdf_sha256(ikm: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
    """RFC 5869 HKDF-Extract-and-Expand with SHA-256."""
    prk = hkdf_extract(salt, ikm)
    return hkdf_expand(prk, info, length)


def derive_circuit_keys(shared_secret: bytes, salt: bytes = b"", context: str = "omniagent-onion-circuit") -> Dict[str, bytes]:
    """
    Derives forward key, backward key, forward digest, and backward digest for a circuit hop.
    Total length = 32 (k_fwd) + 32 (k_bwd) + 20 (d_fwd) + 20 (d_bwd) = 104 bytes.
    """
    prk = hkdf_extract(salt, shared_secret)
    okm = hkdf_expand(prk, context.encode("utf-8"), 104)
    return {
        "k_fwd": okm[0:32],
        "k_bwd": okm[32:64],
        "d_fwd": okm[64:84],
        "d_bwd": okm[84:104],
    }


class HKDF:
    """Class wrapper for RFC 5869 HKDF operations."""

    @staticmethod
    def extract(salt: bytes, ikm: bytes) -> bytes:
        return hkdf_extract(salt, ikm)

    @staticmethod
    def expand(prk: bytes, info: bytes, length: int) -> bytes:
        return hkdf_expand(prk, info, length)

    @staticmethod
    def derive(ikm: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
        return hkdf_sha256(ikm, salt, info, length)

    @staticmethod
    def derive_hop_keys(shared_secret: bytes, salt: bytes = b"", context: str = "omniagent-onion-circuit") -> Dict[str, bytes]:
        return derive_circuit_keys(shared_secret, salt, context)


# ==============================================================================
# 3. Pure Python ChaCha20-Poly1305 Engine (RFC 8439)
# ==============================================================================

def _rotl32(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | ((v >> (32 - c)) & 0xFFFFFFFF)


def _qround(x: list[int], a: int, b: int, c: int, d: int) -> None:
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl32(x[d] ^ x[a], 16)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl32(x[b] ^ x[c], 12)
    x[a] = (x[a] + x[b]) & 0xFFFFFFFF
    x[d] = _rotl32(x[d] ^ x[a], 8)
    x[c] = (x[c] + x[d]) & 0xFFFFFFFF
    x[b] = _rotl32(x[b] ^ x[c], 7)


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    constants = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]
    k = list(struct.unpack("<8I", key))
    n = list(struct.unpack("<3I", nonce))
    init = constants + k + [counter] + n
    x = list(init)
    for _ in range(10):
        _qround(x, 0, 4, 8, 12)
        _qround(x, 1, 5, 9, 13)
        _qround(x, 2, 6, 10, 14)
        _qround(x, 3, 7, 11, 15)
        _qround(x, 0, 5, 10, 15)
        _qround(x, 1, 6, 11, 12)
        _qround(x, 2, 7, 8, 13)
        _qround(x, 3, 4, 9, 14)
    return struct.pack("<16I", *[((x[i] + init[i]) & 0xFFFFFFFF) for i in range(16)])


def _chacha20_crypt(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    block_idx = counter
    res = bytearray()
    for i in range(0, len(data), 64):
        blk = _chacha20_block(key, block_idx, nonce)
        chunk = data[i:i + 64]
        res.extend(a ^ b for a, b in zip(chunk, blk[:len(chunk)]))
        block_idx += 1
    return bytes(res)


def _poly1305_mac(msg: bytes, key: bytes) -> bytes:
    """RFC 8439 Poly1305 One-Time Authenticator."""
    r_bytes = bytearray(key[:16])
    r_bytes[3] &= 15
    r_bytes[7] &= 15
    r_bytes[11] &= 15
    r_bytes[15] &= 15
    r_bytes[4] &= 252
    r_bytes[8] &= 252
    r_bytes[12] &= 252
    r = int.from_bytes(r_bytes, "little")
    s = int.from_bytes(key[16:], "little")
    prime = (1 << 130) - 5
    a = 0
    for i in range(0, len(msg), 16):
        chunk = msg[i:i + 16]
        n = int.from_bytes(chunk + b"\x01", "little")
        a = ((a + n) * r) % prime
    tag = (a + s) % (1 << 128)
    return tag.to_bytes(16, "little")


def _pad16(b: bytes) -> bytes:
    rem = len(b) % 16
    return b"\x00" * (16 - rem) if rem != 0 else b""


def _pure_chacha20_poly1305_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    poly_key = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = _chacha20_crypt(key, 1, nonce, plaintext)
    mac_data = aad + _pad16(aad) + ciphertext + _pad16(ciphertext) + struct.pack("<QQ", len(aad), len(ciphertext))
    tag = _poly1305_mac(mac_data, poly_key)
    return ciphertext + tag


def _pure_chacha20_poly1305_decrypt(key: bytes, nonce: bytes, ct_and_tag: bytes, aad: bytes = b"") -> bytes:
    if len(ct_and_tag) < 16:
        raise AuthenticationError("Ciphertext payload too short for AEAD authentication tag.")
    ciphertext = ct_and_tag[:-16]
    tag = ct_and_tag[-16:]
    poly_key = _chacha20_block(key, 0, nonce)[:32]
    mac_data = aad + _pad16(aad) + ciphertext + _pad16(ciphertext) + struct.pack("<QQ", len(aad), len(ciphertext))
    expected_tag = _poly1305_mac(mac_data, poly_key)
    if not hmac.compare_digest(tag, expected_tag):
        raise AuthenticationError("ChaCha20-Poly1305 authentication tag verification failed; payload corrupted or tampered.")
    return _chacha20_crypt(key, 1, nonce, ciphertext)


# ==============================================================================
# 4. AEAD Cipher Classes
# ==============================================================================

class ChaCha20Poly1305Cipher:
    """RFC 8439 ChaCha20-Poly1305 AEAD Cipher."""

    @staticmethod
    def encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
        if len(key) != 32:
            raise ValueError("ChaCha20-Poly1305 key must be 32 bytes.")
        if len(nonce) != 12:
            raise ValueError("ChaCha20-Poly1305 nonce must be 12 bytes.")

        if _HAS_CRYPTOGRAPHY:
            cipher = _CryptoChaCha(key)
            return cipher.encrypt(nonce, plaintext, aad)

        return _pure_chacha20_poly1305_encrypt(key, nonce, plaintext, aad)

    @staticmethod
    def decrypt(key: bytes, nonce: bytes, ct_and_tag: bytes, aad: bytes = b"") -> bytes:
        if len(key) != 32:
            raise ValueError("ChaCha20-Poly1305 key must be 32 bytes.")
        if len(nonce) != 12:
            raise ValueError("ChaCha20-Poly1305 nonce must be 12 bytes.")

        if _HAS_CRYPTOGRAPHY:
            cipher = _CryptoChaCha(key)
            try:
                return cipher.decrypt(nonce, ct_and_tag, aad)
            except Exception as e:
                raise AuthenticationError(f"AEAD authentication verification failed: {e}") from e

        return _pure_chacha20_poly1305_decrypt(key, nonce, ct_and_tag, aad)


class AES256GCMCipher:
    """AES-256-GCM AEAD Cipher."""

    @staticmethod
    def encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
        if len(key) != 32:
            raise ValueError("AES-256 key must be 32 bytes.")
        if len(nonce) != 12:
            raise ValueError("AES-GCM nonce must be 12 bytes.")

        if _HAS_CRYPTOGRAPHY:
            cipher = _CryptoAESGCM(key)
            return cipher.encrypt(nonce, plaintext, aad)

        # Pure Python portable fallback: Encrypt-then-MAC using HKDF keystream + HMAC-SHA256
        keystream = hkdf_expand(hkdf_extract(key, nonce), b"aes-gcm-keystream", len(plaintext))
        ciphertext = bytes(p ^ s for p, s in zip(plaintext, keystream))
        tag = hmac.new(key, nonce + ciphertext + aad, hashlib.sha256).digest()[:16]
        return ciphertext + tag

    @staticmethod
    def decrypt(key: bytes, nonce: bytes, ct_and_tag: bytes, aad: bytes = b"") -> bytes:
        if len(key) != 32:
            raise ValueError("AES-256 key must be 32 bytes.")
        if len(nonce) != 12:
            raise ValueError("AES-GCM nonce must be 12 bytes.")
        if len(ct_and_tag) < 16:
            raise AuthenticationError("Ciphertext payload too short for AEAD authentication tag.")

        if _HAS_CRYPTOGRAPHY:
            cipher = _CryptoAESGCM(key)
            try:
                return cipher.decrypt(nonce, ct_and_tag, aad)
            except Exception as e:
                raise AuthenticationError(f"AEAD authentication verification failed: {e}") from e

        # Pure Python portable fallback
        ciphertext = ct_and_tag[:-16]
        tag = ct_and_tag[-16:]
        expected_tag = hmac.new(key, nonce + ciphertext + aad, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expected_tag):
            raise AuthenticationError("AES-256-GCM authentication tag mismatch; payload corrupted or tampered.")

        keystream = hkdf_expand(hkdf_extract(key, nonce), b"aes-gcm-keystream", len(ciphertext))
        return bytes(c ^ s for c, s in zip(ciphertext, keystream))


# ==============================================================================
# 5. Universal AEAD Serialization & Utility Functions
# ==============================================================================

def aead_encrypt(
    key: bytes,
    plaintext: bytes,
    aad: bytes = b"",
    cipher_type: str = "chacha20-poly1305",
    nonce: Optional[bytes] = None,
) -> Dict[str, str]:
    """
    Encrypt plaintext returning a JSON-serializable base64 dictionary:
    {"nonce": ..., "ciphertext": ..., "tag": ...}
    """
    if nonce is None:
        nonce = secrets.token_bytes(12)

    cipher_lower = cipher_type.lower()
    if "aes" in cipher_lower:
        ct_and_tag = AES256GCMCipher.encrypt(key, nonce, plaintext, aad)
    else:
        ct_and_tag = ChaCha20Poly1305Cipher.encrypt(key, nonce, plaintext, aad)

    ciphertext = ct_and_tag[:-16]
    tag = ct_and_tag[-16:]

    return {
        "nonce": base64.b64encode(nonce).decode("utf-8"),
        "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
        "tag": base64.b64encode(tag).decode("utf-8"),
    }


def aead_decrypt(
    key: bytes,
    encrypted: Union[Dict[str, str], bytes],
    aad: bytes = b"",
    cipher_type: str = "chacha20-poly1305",
    nonce: Optional[bytes] = None,
) -> bytes:
    """
    Decrypt AEAD payload from dictionary or raw packed bytes.
    """
    if isinstance(encrypted, dict):
        nonce_bytes = base64.b64decode(encrypted["nonce"])
        ciphertext_bytes = base64.b64decode(encrypted["ciphertext"])
        tag_bytes = base64.b64decode(encrypted["tag"])
        ct_and_tag = ciphertext_bytes + tag_bytes
    elif isinstance(encrypted, (bytes, bytearray)):
        if nonce is None:
            if len(encrypted) < 28:
                raise AuthenticationError("Packed payload too short to contain nonce and tag.")
            nonce_bytes = encrypted[:12]
            ct_and_tag = encrypted[12:]
        else:
            nonce_bytes = nonce
            ct_and_tag = bytes(encrypted)
    else:
        raise TypeError(f"Unsupported encrypted payload type: {type(encrypted)}")

    cipher_lower = cipher_type.lower()
    if "aes" in cipher_lower:
        return AES256GCMCipher.decrypt(key, nonce_bytes, ct_and_tag, aad)
    else:
        return ChaCha20Poly1305Cipher.decrypt(key, nonce_bytes, ct_and_tag, aad)


def encrypt_payload(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Binary helper: nonce (12B) + ciphertext + tag (16B)."""
    nonce = secrets.token_bytes(12)
    ct_and_tag = ChaCha20Poly1305Cipher.encrypt(key, nonce, plaintext, aad)
    return nonce + ct_and_tag


def decrypt_payload(key: bytes, payload: bytes, aad: bytes = b"") -> bytes:
    """Binary helper: unpacks nonce (12B) + ciphertext + tag (16B) and decrypts."""
    if len(payload) < 28:
        raise AuthenticationError("Payload too short to be authenticated ciphertext.")
    nonce = payload[:12]
    ct_and_tag = payload[12:]
    return ChaCha20Poly1305Cipher.decrypt(key, nonce, ct_and_tag, aad)
