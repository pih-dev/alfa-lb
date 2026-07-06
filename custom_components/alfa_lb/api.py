"""Alfa Lebanon web-portal client.

The AlfaNet web portal (www.alfa.com.lb/en/account) is a plain ASP.NET MVC
site: cookie-session auth + an anti-forgery token (``__RequestVerificationToken``)
scraped from the login page. No AES envelope, no cert pinning — a full rewrite
of the dead mobile V2/V3 transport. See
``_archive/HomeLab/alfa/2026-07-06-alfa-web-portal-api-map.md``.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime
from typing import Any

import aiohttp

from . import parsers
from .const import (
    CONSUMPTION_PATH,
    EXCHANGE_RATE_PATH,
    EXPIRY_PATH,
    LAST_RECHARGE_PATH,
    LOGIN_PATH,
    PORTAL_BASE,
    SERVICES_PATH,
)

_LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(
    r'name="__RequestVerificationToken"[^>]*value="([^"]+)"'
)
# The login page usually renders the input as name-before-value, but ASP.NET
# can emit value-before-name. Try both orders.
_TOKEN_RE_ALT = re.compile(
    r'value="([^"]+)"[^>]*name="__RequestVerificationToken"'
)

# Markers that indicate we got an HTML page (login/OTP) instead of JSON,
# i.e. the session is not authenticated.
_OTP_MARKERS = ("otp", "verification code", "one-time")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
}


class AlfaAuthError(Exception):
    """Credentials rejected / session cannot be established."""


class AlfaOtpRequired(AlfaAuthError):
    """Login hit an OTP challenge that cannot be answered headlessly."""


class AlfaApiError(Exception):
    """Transport / parsing / non-auth API error."""


def _scrape_token(html: str) -> str | None:
    """Extract ``__RequestVerificationToken`` from the login HTML."""
    for rx in (_TOKEN_RE, _TOKEN_RE_ALT):
        m = rx.search(html or "")
        if m:
            return m.group(1)
    return None


class AlfaPortalClient:
    """Async client for the Alfa web portal.

    Holds a dedicated aiohttp session (its own cookie jar). Logs in once and
    reuses the session cookie across polls; re-logs in only when a read shows
    the session expired.
    """

    def __init__(
        self, session: aiohttp.ClientSession, mobile: str, password: str
    ) -> None:
        self._session = session
        self._mobile = mobile.strip()
        self._password = password
        self._logged_in = False

    @property
    def mobile_number(self) -> str:
        return self._mobile

    async def async_login(self) -> None:
        """Establish an authenticated cookie session."""
        # 1. GET the login page → scrape the anti-forgery token.
        url = f"{PORTAL_BASE}{LOGIN_PATH}"
        params = {"returnUrl": "/en/account"}
        try:
            async with self._session.get(
                url, params=params, headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                login_html = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise AlfaApiError(f"Login page fetch failed: {err}") from err

        token = _scrape_token(login_html)
        if not token:
            raise AlfaApiError("Could not find __RequestVerificationToken on login page")

        # 2. POST credentials.
        form = {
            "__RequestVerificationToken": token,
            "Username": self._mobile,
            "Password": self._password,
            "RememberMe": "false",
        }
        try:
            async with self._session.post(
                url, params=params, data=form, headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                post_body = await resp.text()
                post_status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise AlfaApiError(f"Login POST failed: {err}") from err

        # 3. Verify by probing a JSON endpoint.
        probe = await self._raw_get(CONSUMPTION_PATH)
        if probe.get("_json") is not None:
            self._logged_in = True
            return

        body = probe.get("_text", "") or post_body
        low = body.lower()
        if any(mark in low for mark in _OTP_MARKERS):
            raise AlfaOtpRequired(
                "Portal login requires an OTP code — open www.alfa.com.lb in a "
                "browser to complete sign-in, then reload the integration."
            )
        raise AlfaAuthError(
            f"Login not accepted (status {post_status}); credentials or portal changed"
        )

    async def _raw_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a path; return {'_json': <obj>|None, '_text': <str>, '_status': int}.

        JSON is parsed when the body is valid JSON; otherwise ``_json`` is None
        (usually means an HTML login/OTP redirect = expired session).
        """
        q = {"_": str(int(time.time() * 1000))}
        if params:
            q.update(params)
        url = f"{PORTAL_BASE}{path}"
        try:
            async with self._session.get(
                url, params=q, headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise AlfaApiError(f"GET {path} failed: {err}") from err

        obj: Any = None
        stripped = text.lstrip()
        if stripped[:1] in ("{", "[") or stripped[:1].isdigit():
            import json
            try:
                obj = json.loads(text)
            except ValueError:
                obj = None
        return {"_json": obj, "_text": text, "_status": status}

    async def _get_json(self, path: str) -> Any:
        """GET expecting JSON, with one silent re-login on session expiry."""
        res = await self._raw_get(path)
        if res["_json"] is not None:
            return res["_json"]
        # Session likely expired → re-login once and retry.
        _LOGGER.debug("Alfa %s returned non-JSON; re-logging in", path)
        self._logged_in = False
        await self.async_login()
        res = await self._raw_get(path)
        if res["_json"] is None:
            raise AlfaApiError(f"{path} did not return JSON after re-login")
        return res["_json"]

    async def async_validate(self) -> dict[str, Any]:
        """Verify credentials; return identity block for the config entry."""
        await self.async_login()
        consumption = await self._get_json(CONSUMPTION_PATH)
        parsed = parsers.parse_consumption(consumption)
        return {
            "MobileNumberValue": parsed.get("mobile") or self._mobile,
            "TypeValue": parsed.get("type"),
            "SubTypeValue": parsed.get("subtype"),
        }

    async def async_get_account_data(self) -> dict[str, Any]:
        """Fetch consumption (mandatory) + services/expiry/recharge/rate
        (best-effort) and merge into the coordinator model."""
        if not self._logged_in:
            await self.async_login()

        consumption = await self._get_json(CONSUMPTION_PATH)
        result = parsers.parse_consumption(consumption)

        # Best-effort extras: a failure of any one must not fail the poll.
        services = await self._safe_get_json(SERVICES_PATH)
        expiry = await self._safe_get_json(EXPIRY_PATH)
        recharge = await self._safe_get_json(LAST_RECHARGE_PATH)
        rate = await self._safe_get_json(EXCHANGE_RATE_PATH)

        svc = parsers.parse_services(services) if services is not None else {
            "active_bundle": None, "catalog": [], "simultaneous_activation": False,
        }
        active = svc.get("active_bundle")

        result["catalog"] = svc.get("catalog", [])
        result["simultaneous_activation"] = svc.get("simultaneous_activation", False)
        result["active_bundle_name"] = active.get("text") if active else None
        result["active_bundle_price"] = active.get("price_usd") if active else None

        # plan_name + validity: pick the active bundle's matching consumption
        # entry when we can, else the newest-expiry data bundle.
        result["plan_name"] = _active_display_name(result["bundles"], active)
        result["validity"] = _active_validity(result["bundles"], active)

        result["days_until_expiry"] = _coerce_int(expiry)

        if isinstance(recharge, dict):
            result["last_recharge_amount"] = parsers.parse_money(recharge.get("Amount"))
            result["last_recharge_date"] = _parse_recharge_date(recharge.get("Date"))
        else:
            result["last_recharge_amount"] = None
            result["last_recharge_date"] = None

        result["exchange_rate_lbp"] = _coerce_int(rate)
        return result

    async def _safe_get_json(self, path: str) -> Any:
        try:
            return await self._get_json(path)
        except AlfaApiError as err:
            _LOGGER.warning("Alfa best-effort GET %s failed: %s", path, err)
            return None


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
    """DisplayName of the consumption bundle matching the active service bundle
    (by GB size), else the DisplayName of the newest-expiry data bundle."""
    if active and active.get("gb") is not None:
        for b in bundles:
            if b.get("total_gb") == float(active["gb"]):
                return b.get("name")
    data_bundles = [b for b in bundles if b.get("usage_type") == "data" and b.get("expiry")]
    if data_bundles:
        return max(data_bundles, key=lambda b: b["expiry"]).get("name")
    return bundles[0].get("name") if bundles else None


def _active_validity(bundles: list[dict[str, Any]], active: dict[str, Any] | None):
    """ISO expiry string → aware datetime for the active/newest data bundle."""
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
    """``getlastrecharge`` Date → aware datetime, or None when empty."""
    if not raw:
        return None
    dt = parsers.parse_portal_dt(raw)
    if dt:
        return dt
    # Some portals return DD/MM/YYYY here — tolerate it.
    parts = str(raw).strip().split("/")
    if len(parts) == 3:
        try:
            d, m, y = (int(x) for x in parts)
            return datetime.combine(date(y, m, d), datetime.min.time()).astimezone()
        except ValueError:
            return None
    return None
