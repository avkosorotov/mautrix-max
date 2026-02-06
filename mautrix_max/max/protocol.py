"""Binary protocol utilities for Max WebSocket (msgpack + lz4).

Currently the bridge uses JSON-over-WebSocket (as proven by PyMax).
This module provides utilities for the binary format as a fallback
in case Max deprecates the JSON protocol.

Binary packet format:
  Header: [version:1][cmd:2][seq:1][opcode:2][length+compression:4] = 10 bytes
  Payload: MessagePack encoded, optionally LZ4-compressed
"""

from __future__ import annotations

import struct
from typing import Any, Optional

# These imports are optional -- only needed if binary mode is activated
try:
    import lz4.frame
    import msgpack

    HAS_BINARY_DEPS = True
except ImportError:
    HAS_BINARY_DEPS = False

# Header format: version(1) + cmd(2) + seq(1) + opcode(2) + length_and_flags(4)
HEADER_FORMAT = ">BHBHH"  # Big-endian: uint8, uint16, uint8, uint16, uint16
HEADER_SIZE = 8  # Simplified header

# Compression flag in the length field
COMPRESSION_FLAG = 0x80000000
LENGTH_MASK = 0x7FFFFFFF


def pack_binary_message(
    opcode: int,
    seq: int,
    data: Any,
    *,
    compress: bool = False,
    version: int = 1,
    cmd: int = 0,
) -> bytes:
    """Pack a message into the binary wire format.

    Args:
        opcode: The operation code.
        seq: Sequence number for request/response matching.
        data: Python object to encode as MessagePack payload.
        compress: Whether to LZ4-compress the payload.
        version: Protocol version byte.
        cmd: Command byte.

    Returns:
        The binary-encoded message bytes.
    """
    if not HAS_BINARY_DEPS:
        raise RuntimeError("msgpack and lz4 are required for binary protocol")

    payload = msgpack.packb(data, use_bin_type=True)

    if compress:
        payload = lz4.frame.compress(payload)

    length = len(payload)
    if compress:
        length |= COMPRESSION_FLAG

    header = struct.pack(">BBHH", version, seq & 0xFF, opcode, length & 0xFFFF)
    return header + payload


def unpack_binary_message(raw: bytes) -> tuple[int, int, Any]:
    """Unpack a binary message from the wire format.

    Args:
        raw: The raw bytes received from the WebSocket.

    Returns:
        A tuple of (opcode, seq, decoded_payload).
    """
    if not HAS_BINARY_DEPS:
        raise RuntimeError("msgpack and lz4 are required for binary protocol")

    if len(raw) < 6:
        raise ValueError(f"Message too short: {len(raw)} bytes")

    version, seq, opcode, length = struct.unpack(">BBHH", raw[:6])

    compressed = bool(length & 0x8000)
    payload_length = length & 0x7FFF

    payload_bytes = raw[6:6 + payload_length]

    if compressed:
        payload_bytes = lz4.frame.decompress(payload_bytes)

    data = msgpack.unpackb(payload_bytes, raw=False)
    return opcode, seq, data
