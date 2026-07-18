"""Time-ordered UUIDv7 identifiers for durable operational records."""

from __future__ import annotations

import secrets
import time
from uuid import UUID


def uuid7() -> str:
    milliseconds = int(time.time() * 1_000)
    value = (milliseconds << 80) | (0x7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    return str(UUID(int=value))
