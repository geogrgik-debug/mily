"""Identifiers and payload fingerprints."""

from __future__ import annotations

import hashlib
import os
import secrets

# Crockford base32: no I, L, O, U. Lexicographic order matches numeric order.
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_B32[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_event_id(wall_ns: int, *, rand: bytes | None = None) -> str:
    """A ULID: 48-bit millisecond timestamp then 80 random bits, base32.

    Sorting event ids sorts them by receipt time, which makes an append-only
    log browsable without reading it, and makes duplicate ids effectively
    impossible across concurrent collector processes.
    """
    ms = wall_ns // 1_000_000
    if not 0 <= ms < (1 << 48):
        raise ValueError(f"timestamp out of ULID range: {wall_ns}")
    entropy = rand if rand is not None else secrets.token_bytes(10)
    if len(entropy) != 10:
        raise ValueError("ULID entropy must be 10 bytes")
    return _encode(ms, 10) + _encode(int.from_bytes(entropy, "big"), 16)


def event_id_time_ms(event_id: str) -> int:
    """Recover the millisecond timestamp from a ULID."""
    value = 0
    for ch in event_id[:10]:
        idx = _B32.find(ch.upper())
        if idx < 0:
            raise ValueError(f"not a ULID: {event_id!r}")
        value = (value << 5) | idx
    return value


def payload_hash(payload: bytes | str) -> str:
    """Stable fingerprint of a raw payload.

    Used to drop the duplicates that polling feeds return on every tick
    without having to parse them, and to prove after the fact that a parsed
    row came from the bytes the log claims it did.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def new_run_id(wall_ns: int) -> str:
    """Identifier for one collector process lifetime."""
    return f"run-{wall_ns // 1_000_000_000}-{os.getpid()}-{secrets.token_hex(3)}"
