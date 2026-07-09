"""Alfa Lebanon transport — file reader.

The portal login is gated by an F5/Shape anti-bot that no headless HTTP client
can pass, so login + reads are done by the separate `alfa-session` add-on (a
real headless-Chromium browser). That add-on writes the raw portal JSON to
/share/alfa_lb/latest.json; this module reads that file and feeds the existing
parsers. See docs/superpowers/specs/2026-07-07-alfa-session-provider-design.md.

No aiohttp, no pycryptodome — the integration performs no network I/O.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant

from . import parsers
from .const import SESSION_FILE, STALE_AFTER

_LOGGER = logging.getLogger(__name__)


class AlfaAuthError(Exception):
    """Session provider reports it cannot authenticate (add-on owns the fix)."""


class AlfaOtpRequired(AlfaAuthError):
    """Kept for import stability with older callers."""


class AlfaApiError(Exception):
    """Session file missing / stale / unreadable / add-on error status."""


def build_account_model(
    consumption: Any,
    services: Any,
    expiry: Any,
    recharge: Any,
    exchange: Any,
) -> dict[str, Any]:
    """Merge raw portal bodies into the coordinator model (transport-agnostic)."""
    result = parsers.parse_consumption(consumption or {})

    svc = parsers.parse_services(services) if services is not None else {
        "active_bundle": None, "catalog": [], "simultaneous_activation": False,
    }
    active = svc.get("active_bundle")

    result["catalog"] = svc.get("catalog", [])
    result["simultaneous_activation"] = svc.get("simultaneous_activation", False)
    result["active_bundle_name"] = active.get("text") if active else None
    result["active_bundle_price"] = active.get("price_usd") if active else None
    result["plan_name"] = _active_display_name(result["bundles"], active)
    result["validity"] = _active_validity(result["bundles"], active)

    result["days_until_expiry"] = _coerce_int(expiry)

    if isinstance(recharge, dict):
        result["last_recharge_amount"] = parsers.parse_money(recharge.get("Amount"))
        result["last_recharge_date"] = _parse_recharge_date(recharge.get("Date"))
    else:
        result["last_recharge_amount"] = None
        result["last_recharge_date"] = None

    result["exchange_rate_lbp"] = _coerce_int(exchange)
    return result


class AlfaFileClient:
    """Reads the add-on's /share/alfa_lb/latest.json and parses it.

    Fails safe: any missing/stale/error/auth status raises so the coordinator
    marks sensors `unavailable` — the same surface as before (never crashes HA).
    """

    def __init__(self, hass: HomeAssistant, path: str, mobile: str) -> None:
        self._hass = hass
        self._path = path or SESSION_FILE
        self._mobile = (mobile or "").strip()

    @property
    def mobile_number(self) -> str:
        return self._mobile

    async def _read_doc(self) -> dict[str, Any]:
        def _read() -> dict[str, Any]:
            with open(self._path, encoding="utf-8") as fh:
                return json.load(fh)

        try:
            return await self._hass.async_add_executor_job(_read)
        except FileNotFoundError as err:
            raise AlfaApiError(f"session file missing: {self._path}") from err
        except (OSError, ValueError) as err:
            raise AlfaApiError(f"session file unreadable: {err}") from err

    def _validated_data(self, doc: dict[str, Any]) -> dict[str, Any]:
        status = doc.get("status")
        if status in ("auth_required", "auth_failed"):
            raise AlfaAuthError(f"session provider status={status}")
        if status != "ok":
            raise AlfaApiError(f"session provider status={status}")
        raw = doc.get("fetched_at")
        try:
            fetched = datetime.fromisoformat(raw)
        except (TypeError, ValueError) as err:
            raise AlfaApiError(f"bad fetched_at: {raw!r}") from err
        age = datetime.now().astimezone() - fetched
        if age > STALE_AFTER:
            raise AlfaApiError(f"session file stale by {age} (>{STALE_AFTER})")
        return doc.get("data") or {}

    async def async_validate(self) -> dict[str, Any]:
        doc = await self._read_doc()
        data = self._validated_data(doc)
        parsed = parsers.parse_consumption(data.get("consumption") or {})
        return {
            "MobileNumberValue": parsed.get("mobile") or self._mobile,
            "TypeValue": parsed.get("type"),
            "SubTypeValue": parsed.get("subtype"),
        }

    async def async_get_account_data(self) -> dict[str, Any]:
        doc = await self._read_doc()
        data = self._validated_data(doc)
        return build_account_model(
            data.get("consumption"),
            data.get("services"),
            data.get("expiry"),
            data.get("recharge"),
            data.get("exchange"),
        )


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _active_display_name(bundles: list[dict[str, Any]], active: dict[str, Any] | None) -> str | None:
    if active and active.get("gb") is not None:
        for b in bundles:
            if b.get("total_gb") == float(active["gb"]):
                return b.get("name")
    data_bundles = [b for b in bundles if b.get("usage_type") == "data" and b.get("expiry")]
    if data_bundles:
        return max(data_bundles, key=lambda b: b["expiry"]).get("name")
    return bundles[0].get("name") if bundles else None


def _active_validity(bundles: list[dict[str, Any]], active: dict[str, Any] | None):
    iso: str | None = None
    if active and active.get("gb") is not None:
        for b in bundles:
            if b.get("total_gb") == float(active["gb"]):
                iso = b.get("expiry")
                break
    if iso is None:
        data_bundles = [b for b in bundles if b.get("usage_type") == "data" and b.get("expiry")]
        if data_bundles:
            iso = max(data_bundles, key=lambda b: b["expiry"])["expiry"]
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _parse_recharge_date(raw: Any):
    if not raw:
        return None
    dt = parsers.parse_portal_dt(raw)
    if dt:
        return dt
    parts = str(raw).strip().split("/")
    if len(parts) == 3:
        try:
            d, m, y = (int(x) for x in parts)
            return datetime.combine(date(y, m, d), datetime.min.time()).astimezone()
        except ValueError:
            return None
    return None
