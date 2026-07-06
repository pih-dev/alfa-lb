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


def _to_mb(value: Any, unit: str | None) -> float | None:
    """Normalise a portal amount+unit to DECIMAL MB (GB * 1000)."""
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    u = (unit or "").upper()
    if u == "GB":
        return num * 1000
    if u == "KB":
        return num / 1000
    return num  # already MB (or unknown → treat as MB)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def parse_consumption(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse ``getconsumptionasync`` into the coordinator model.

    ``FreeUnitsValue[]`` holds one entry per active bundle. Aggregates
    (data_*_mb) sum ONLY ``UsageType == "data"`` bundles so voice/SMS
    freebies never inflate the data gauge.
    """
    if not isinstance(payload, dict):
        payload = {}

    bundles: list[dict[str, Any]] = []
    total_mb = used_mb = 0.0
    saw_data = False

    for fu in payload.get("FreeUnitsValue") or []:
        if not isinstance(fu, dict):
            continue
        usage = (fu.get("UsageType") or "").lower()
        t_mb = _to_mb(fu.get("TotalAmount"), fu.get("TotalUnit"))
        u_mb = _to_mb(fu.get("UsedAmount"), fu.get("UsedUnit"))
        r_mb = (t_mb - u_mb) if (t_mb is not None and u_mb is not None) else None
        validity = parse_portal_dt(fu.get("ValidityDate"))
        expiry = parse_portal_dt(fu.get("ExpiryTime"))
        bundles.append({
            "name": fu.get("DisplayName"),
            "usage_type": usage,
            "total_mb": t_mb,
            "used_mb": u_mb,
            "remaining_mb": r_mb,
            "total_gb": round(t_mb / 1000, 2) if t_mb is not None else None,
            "used_gb": round(u_mb / 1000, 2) if u_mb is not None else None,
            "remaining_gb": round(r_mb / 1000, 2) if r_mb is not None else None,
            "validity": _iso(validity),
            "expiry": _iso(expiry),
            "offer": fu.get("LinkedOfferName"),
        })
        if usage == "data" and t_mb is not None and u_mb is not None:
            saw_data = True
            total_mb += t_mb
            used_mb += u_mb

    return {
        "balance_usd": parse_money(payload.get("CurrentBalanceValue")),
        "balance_raw": payload.get("CurrentBalanceValue"),
        "mobile": payload.get("MobileNumberValue"),
        "type": payload.get("TypeValue"),
        "subtype": payload.get("SubTypeValue"),
        "bundles": bundles,
        "data_total_mb": total_mb if saw_data else None,
        "data_used_mb": used_mb if saw_data else None,
        "data_remaining_mb": (total_mb - used_mb) if saw_data else None,
    }
