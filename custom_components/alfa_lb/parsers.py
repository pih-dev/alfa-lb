"""Pure parsers for the Alfa web-portal JSON API.

No aiohttp / Home Assistant imports — importable and unit-testable standalone.
All money is USD; all data is normalised to DECIMAL MB (GB * 1000) to match
the labels the AlfaNet app shows.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_MONEY_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_money(raw: Any) -> float | None:
    """`"$ 19.41"` -> ``19.41``. Bare numbers pass through; empty -> None."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _MONEY_RE.search(str(raw))
    return float(m.group(0)) if m else None


def parse_portal_dt(raw: str | None) -> datetime | None:
    """Portal timestamp ``"YYYYMMDDhhmmss"`` -> tz-aware local datetime.

    The portal returns bundle validity/expiry as a 14-digit string, e.g.
    ``"20260804235959"``. Returns None on any malformed input so the
    coordinator can degrade gracefully rather than crash.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) != 14 or not s.isdigit():
        return None
    try:
        naive = datetime.strptime(s, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return naive.astimezone()
