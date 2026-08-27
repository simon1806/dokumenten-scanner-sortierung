"""Stabiles, datensparsames Schema fuer maschinenlesbare Logereignisse."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any


EVENT_SCHEMA_VERSION = 2
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SESSION_ID = uuid.uuid4().hex
_PROCESS_STARTED = time.monotonic()


def current_session_id() -> str:
    """Return the immutable identifier shared by all events of this process."""

    return _SESSION_ID


def process_uptime_seconds() -> int:
    return max(0, round(time.monotonic() - _PROCESS_STARTED))


def safe_event_value(value: Any) -> str:
    """Keep a value on one delimiter-safe log line."""

    if value is None:
        return "keine"
    if isinstance(value, bool):
        return "ja" if value else "nein"
    return " ".join(str(value).replace(";", ",").splitlines()).strip() or "leer"


def structured_event(label: str, event_code: str, **fields: Any) -> str:
    """Build one backwards-readable line with stable schema and event identity."""

    if not _FIELD_NAME_RE.fullmatch(event_code):
        raise ValueError(f"Ungueltiger Ereigniscode: {event_code!r}")
    parts = [
        label,
        f"schema={EVENT_SCHEMA_VERSION}",
        f"ereignis={event_code}",
        f"sitzung={_SESSION_ID}",
    ]
    for key, value in fields.items():
        if not _FIELD_NAME_RE.fullmatch(key):
            raise ValueError(f"Ungueltiger Feldname: {key!r}")
        parts.append(f"{key}={safe_event_value(value)}")
    return "; ".join(parts)
