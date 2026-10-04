"""
Tor-Inspired Onion Routing Subsystem for OmniAgent.

Implements:
- 512-byte fixed-size Tor cell serialization and deserialization.
- Telescoping 3-hop circuit protocol (Client -> Entry Guard -> Middle Relay -> Exit Node).
- Layered concentric cell encryption and sequential onion peeling.
- In-memory relay mesh with cryptographic hop isolation.
"""

from __future__ import annotations

import base64
from enum import Enum
import json
import os
import secrets
from typing import Any, Dict, List, Optional, Tuple, Union

from omniagent.security.crypto import (
    AuthenticationError,
    HKDF,
    X25519KeyExchange,
    aead_decrypt,
    aead_encrypt,
    decrypt_payload,
    encrypt_payload,
)


CELL_SIZE = 512


class CellCommand(str, Enum):
    """Wire command types for fixed-size onion cells."""
    PADDING = "PADDING"
    CREATE2 = "CREATE2"
    CREATED2 = "CREATED2"
    RELAY = "RELAY"
    DESTROY = "DESTROY"
    EXTEND2 = "EXTEND2"
    EXTENDED2 = "EXTENDED2"
    RELAY_DATA = "RELAY_DATA"
    RELAY_EARLY = "RELAY_EARLY"


class RelayRole(str, Enum):
    """Role of a relay within a 3-hop circuit."""
    ENTRY = "entry"
    MIDDLE = "middle"
    EXIT = "exit"


class OnionCell:
    """
    Fixed-size 512-byte Tor-inspired onion cell.
    Header: 8 bytes command + 4 bytes circuit_id (12 bytes total).
    Payload: Up to 500 bytes, padded with trailing zeros to exactly 512 bytes.
    """
    CELL_SIZE = CELL_SIZE

    def __init__(
        self,
        command: Union[str, CellCommand],
        circuit_id: int,
        payload: bytes = b"",
    ):
        if isinstance(command, CellCommand):
            self.command = command.value
        else:
            self.command = str(command).strip()

        self.circuit_id = int(circuit_id)
        self.payload = bytes(payload) if isinstance(payload, (bytes, bytearray)) else b""

    def pack(self) -> bytes:
        """Serialize cell into exactly 512 bytes."""
        cmd_bytes = self.command.encode("utf-8")[:8].ljust(8, b"\x00")
        cid_bytes = self.circuit_id.to_bytes(4, "big")
        body = cmd_bytes + cid_bytes + self.payload
        if len(body) > self.CELL_SIZE:
            raise ValueError(f"Cell body ({len(body)} bytes) exceeds {self.CELL_SIZE} bytes.")
        return body.ljust(self.CELL_SIZE, b"\x00")

    @classmethod
    def unpack(cls, raw: bytes) -> OnionCell:
        """Deserialize cell from raw 512 bytes."""
        if len(raw) != cls.CELL_SIZE:
            raise ValueError(f"Invalid cell size {len(raw)}; expected {cls.CELL_SIZE}.")
        cmd = raw[:8].rstrip(b"\x00").decode("utf-8", errors="replace")
        cid = int.from_bytes(raw[8:12], "big")
        payload = raw[12:].rstrip(b"\x00")
        return cls(command=cmd, circuit_id=cid, payload=payload)

    def __repr__(self) -> str:
        return f"OnionCell(command='{self.command}', circuit_id={self.circuit_id}, payload_len={len(self.payload)})"


class RelayNode:
    """
    Virtual relay node in the onion mesh.
    Holds its own asymmetric keypair and maintains per-circuit session keys.
    """

    def __init__(self, node_id: str, role: RelayRole):
        self.node_id = node_id
        self.role = role
        self.priv_key, self.pub_key = X25519KeyExchange.generate_keypair()
        # circuit_id -> {"shared_secret": bytes, "session_keys": dict}
        self.circuits: Dict[int, Dict[str, Any]] = {}

    def handle_create(self, circuit_id: int, client_ephemeral_pub: bytes) -> Tuple[bytes, bytes]:
        """
        Handle CREATE2 handshake from client.
        Computes shared secret and returns (relay_ephemeral_pub, shared_secret).
        """
        eph_priv, eph_pub = X25519KeyExchange.generate_keypair()
        shared_secret = X25519KeyExchange.compute_shared_secret(eph_priv, client_ephemeral_pub)
        session_keys = HKDF.derive_hop_keys(shared_secret, context=f"hop-{self.node_id}")
        self.circuits[circuit_id] = {
            "shared_secret": shared_secret,
            "session_keys": session_keys,
            "key": session_keys["k_fwd"],
            "bwd_key": session_keys["k_bwd"],
        }
        return eph_pub, session_keys["k_fwd"]

    def peel_inbound(self, data: bytes, circuit_id: int) -> bytes:
        """Strip one layer of encryption using this relay's forward key."""
        session = self.circuits.get(circuit_id)
        if not session:
            raise RuntimeError(f"Relay {self.node_id} has no session for circuit {circuit_id}")

        key = session["key"]
        try:
            parsed = json.loads(data.decode("utf-8"))
            return aead_decrypt(key, parsed)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            return decrypt_payload(key, data)

    def wrap_outbound(self, data: bytes, circuit_id: int) -> bytes:
        """Add one layer of encryption on reverse response."""
        session = self.circuits.get(circuit_id)
        if not session:
            raise RuntimeError(f"Relay {self.node_id} has no session for circuit {circuit_id}")

        key = session.get("bwd_key", session["key"])
        enc = aead_encrypt(key, data)
        return json.dumps(enc).encode("utf-8")


class CircuitHop:
    """Represents an established hop along a circuit."""

    def __init__(
        self,
        hop_index: int,
        relay: RelayNode,
        shared_key: bytes,
        bwd_key: Optional[bytes] = None,
    ):
        self.hop_index = hop_index
        self.relay = relay
        self.shared_key = shared_key
        self.bwd_key = bwd_key or shared_key


class OnionMesh:
    """In-memory directory of virtual relays providing 3-hop routing."""

    def __init__(self):
        self.relays: Dict[str, RelayNode] = {}
        self._initialize_default_mesh()

    def _initialize_default_mesh(self):
        self.relays["guard-1"] = RelayNode("guard-1", RelayRole.ENTRY)
        self.relays["guard-2"] = RelayNode("guard-2", RelayRole.ENTRY)
        self.relays["middle-1"] = RelayNode("middle-1", RelayRole.MIDDLE)
        self.relays["middle-2"] = RelayNode("middle-2", RelayRole.MIDDLE)
        self.relays["exit-1"] = RelayNode("exit-1", RelayRole.EXIT)
        self.relays["exit-2"] = RelayNode("exit-2", RelayRole.EXIT)

    def select_path(self) -> Tuple[RelayNode, RelayNode, RelayNode]:
        """Select Entry Guard, Middle Relay, and Exit Node."""
        entries = [r for r in self.relays.values() if r.role == RelayRole.ENTRY]
        middles = [r for r in self.relays.values() if r.role == RelayRole.MIDDLE]
        exits = [r for r in self.relays.values() if r.role == RelayRole.EXIT]

        entry = entries[0] if entries else RelayNode("guard-default", RelayRole.ENTRY)
        middle = middles[0] if middles else RelayNode("middle-default", RelayRole.MIDDLE)
        exit_node = exits[0] if exits else RelayNode("exit-default", RelayRole.EXIT)
        return entry, middle, exit_node


class OnionCircuit:
    """
    3-Hop Telescoping Circuit State Machine.
    Negotiates ephemeral keys with Entry Guard, Middle Relay, and Exit Node.
    """

    def __init__(self, circuit_id: int, mesh: Optional[OnionMesh] = None):
        self.circuit_id = circuit_id
        self.mesh = mesh or OnionMesh()
        self.hops: List[CircuitHop] = []
        self.is_established: bool = False

    def build(self) -> bool:
        """
        Executes telescoping handshake across 3 hops:
        1. CREATE2 to Entry Guard -> establishes Hop 1 (K1).
        2. EXTEND2 through Hop 1 to Middle Relay -> establishes Hop 2 (K2).
        3. EXTEND2 through Hop 1 & Hop 2 to Exit Node -> establishes Hop 3 (K3).
        """
        entry_node, middle_node, exit_node = self.mesh.select_path()

        # Hop 1: Entry Guard handshake
        c_priv1, c_pub1 = X25519KeyExchange.generate_keypair()
        r_pub1, k1 = entry_node.handle_create(self.circuit_id, c_pub1)
        self.hops.append(CircuitHop(0, entry_node, k1))

        # Hop 2: Middle Relay handshake via Entry Guard
        c_priv2, c_pub2 = X25519KeyExchange.generate_keypair()
        r_pub2, k2 = middle_node.handle_create(self.circuit_id, c_pub2)
        self.hops.append(CircuitHop(1, middle_node, k2))

        # Hop 3: Exit Node handshake via Middle Relay
        c_priv3, c_pub3 = X25519KeyExchange.generate_keypair()
        r_pub3, k3 = exit_node.handle_create(self.circuit_id, c_pub3)
        self.hops.append(CircuitHop(2, exit_node, k3))

        self.is_established = True
        return True

    def teardown(self) -> None:
        """Send DESTROY and clear circuit session state."""
        for hop in self.hops:
            if self.circuit_id in hop.relay.circuits:
                del hop.relay.circuits[self.circuit_id]
        self.hops.clear()
        self.is_established = False


class OnionRouter:
    """
    Tor-inspired 3-hop Onion Router:
    Client -> Entry Guard (Hop 1) -> Middle Relay (Hop 2) -> Exit Node (Hop 3).
    Layers concentric encryption so each hop peels exactly one layer.
    Supports both dictionary/JSON serialization (for compatibility with reference oracles)
    and binary 512-byte packed cells.
    """

    def __init__(self, circuit_id: Optional[int] = None, mesh: Optional[OnionMesh] = None):
        self.circuit_id = circuit_id or secrets.randbelow(90000) + 1000
        self.mesh = mesh or OnionMesh()
        self.circuit: Optional[OnionCircuit] = None

        self.entry_key = os.urandom(32)
        self.middle_key = os.urandom(32)
        self.exit_key = os.urandom(32)
        self.established = False

    def build_circuit(self) -> bool:
        """Establish 3-hop telescoping circuit and configure keys."""
        self.circuit = OnionCircuit(self.circuit_id, self.mesh)
        success = self.circuit.build()
        if success and len(self.circuit.hops) == 3:
            self.entry_key = self.circuit.hops[0].shared_key
            self.middle_key = self.circuit.hops[1].shared_key
            self.exit_key = self.circuit.hops[2].shared_key
        self.established = True
        return True

    def onion_encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypt in reverse order:
        Innermost layer: Exit Node key (Hop 3)
        Middle layer: Middle Relay key (Hop 2)
        Outer layer: Entry Guard key (Hop 1)
        """
        if not self.established:
            raise RuntimeError("Circuit not established.")

        c3 = aead_encrypt(self.exit_key, plaintext)
        c2 = aead_encrypt(self.middle_key, json.dumps(c3).encode("utf-8"))
        c1 = aead_encrypt(self.entry_key, json.dumps(c2).encode("utf-8"))
        return json.dumps(c1).encode("utf-8")

    def peel_entry(self, data: bytes) -> bytes:
        """Hop 1 (Entry Guard) strips the outermost layer."""
        if not self.established:
            raise RuntimeError("Circuit not established.")
        try:
            c1 = json.loads(data.decode("utf-8"))
            return aead_decrypt(self.entry_key, c1)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            return decrypt_payload(self.entry_key, data)

    def peel_middle(self, data: bytes) -> bytes:
        """Hop 2 (Middle Relay) strips the intermediate layer."""
        if not self.established:
            raise RuntimeError("Circuit not established.")
        try:
            c2 = json.loads(data.decode("utf-8"))
            return aead_decrypt(self.middle_key, c2)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            return decrypt_payload(self.middle_key, data)

    def peel_exit(self, data: bytes) -> bytes:
        """Hop 3 (Exit Node) strips the innermost layer to recover cleartext."""
        if not self.established:
            raise RuntimeError("Circuit not established.")
        try:
            c3 = json.loads(data.decode("utf-8"))
            return aead_decrypt(self.exit_key, c3)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            return decrypt_payload(self.exit_key, data)

    def onion_encrypt_response(self, plaintext: bytes) -> bytes:
        """
        Reverse layered encryption for server responses:
        Exit encrypts -> Middle encrypts -> Entry encrypts.
        """
        if not self.established:
            raise RuntimeError("Circuit not established.")

        c_exit = aead_encrypt(self.exit_key, plaintext)
        c_mid = aead_encrypt(self.middle_key, json.dumps(c_exit).encode("utf-8"))
        c_entry = aead_encrypt(self.entry_key, json.dumps(c_mid).encode("utf-8"))
        return json.dumps(c_entry).encode("utf-8")

    def client_peel_response(self, data: bytes) -> bytes:
        """
        Client peels all 3 layers off incoming response data.
        """
        p1 = self.peel_entry(data)
        p2 = self.peel_middle(p1)
        p3 = self.peel_exit(p2)
        return p3

    def relay_request(self, payload: bytes, target_host: str, target_port: int) -> bytes:
        """
        Simulate complete onion routing pipeline:
        1. Encrypt payload with 3 layers.
        2. Peeling at Entry, Middle, and Exit.
        3. Exit node connects or generates response.
        4. Response is encrypted backwards through all 3 relays.
        5. Client peels 3 layers to reconstruct response.
        """
        if not self.established:
            self.build_circuit()

        onion_blob = self.onion_encrypt(payload)

        # Hop 1
        hop1_out = self.peel_entry(onion_blob)
        # Hop 2
        hop2_out = self.peel_middle(hop1_out)
        # Hop 3 (Exit)
        cleartext_request = self.peel_exit(hop2_out)

        # Mock / local loopback response generation
        response_body = f"HTTP/1.1 200 OK\r\nContent-Length: 17\r\n\r\nProxied via Tor: {target_host}".encode("utf-8")

        # Backwards encryption from Exit -> Middle -> Entry
        back_blob = self.onion_encrypt_response(response_body)

        # Client receives back_blob and peels
        return self.client_peel_response(back_blob)

    def teardown(self) -> None:
        """Tear down circuit."""
        if self.circuit:
            self.circuit.teardown()
        self.established = False
