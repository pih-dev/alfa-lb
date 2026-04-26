"""Alfa Lebanon mobile-API client.

Reverse-engineered from the official Android app (com.apps2you.alfa v5.2.86):
all calls POST to ``/V2/Default`` with an AES-256-CBC encrypted JSON body
wrapped as ``{"Data": "<base64 ciphertext>"}``. The plaintext carries the
operation name in a ``Method`` field plus a few platform metadata fields.
The user logs in with phone number + password — no captcha, no cookies.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from datetime import date, datetime
from typing import Any

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

_LOGGER = logging.getLogger(__name__)

API_URL = "https://wsitranslator-live.alfa.com.lb/V2/Default"

_AES_KEY = b"CXLI1C3iCLHRQk5MH9aDvdYYQfAFlte2"
_AES_IV = b"t0dmo_999@999---"

# V3 transport — used by Services/Get + Bundle/* + Services/{Sub,Unsub}scribe
# + PinCode/Get. Different base URL from V2 (mobapirules-live, not
# wsitranslator-live) but — per jadx CrossPlatformEncryptor key/IV
# Caesar-shift resolution — the SAME AES key + IV as V2. The OkHttp
# interceptor (jadx Z1/f.java) rewrites every outgoing V3 POST to
# /V3/Default/Get and splits the original endpoint name into ``Method`` /
# ``ActionID`` body fields. EMPIRICAL FINDING (2026-04-26 live test): the
# server WAF rejects ``application/x-www-form-urlencoded`` bodies but
# accepts the same V2-style JSON envelope ``{"Data": "<ciphertext>"}`` —
# so the actual transport diverges from f.java's FormBody construction.
# The plaintext envelope inside ``Data`` matches f.java exactly though.
V3_API_URL = "https://mobapirules-live.alfa.com.lb/V3/Default/Get"

_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 16; Pixel 7a Build/CP1A.260305.018)",
    "Language": "en",
}

# Status codes returned in the decrypted payload (from the app's StatusCodes).
_STATUS_OK = {2000, 8081, 8090, 8101}
_STATUS_AUTH_FAILED = {3000, 3001, 3002, 4000, 4001, 4002}


class AlfaAuthError(Exception):
    """Credentials rejected by the Alfa API."""


class AlfaApiError(Exception):
    """Transport / parsing / non-auth API error."""


def _encrypt(plaintext: str) -> str:
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    return base64.b64encode(cipher.encrypt(pad(plaintext.encode(), 16))).decode()


def _decrypt(ciphertext: str) -> str:
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    return unpad(cipher.decrypt(base64.b64decode(ciphertext)), 16).decode()




def _parse_money(raw: Any) -> float | None:
    """`"$ 63.05"` → ``63.05``. Bare numbers pass through."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    import re
    m = re.search(r"-?\d+(?:\.\d+)?", str(raw))
    return float(m.group(0)) if m else None


def _parse_dmy(raw: str | None) -> date | None:
    """`"19/05/2026"` → ``date(2026, 5, 19)``."""
    if not raw:
        return None
    parts = raw.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        d, m, y = (int(x) for x in parts)
        return date(y, m, d)
    except ValueError:
        return None


def _to_mb(value: str | None, unit: str | None) -> float | None:
    """Normalise to decimal MB so HA's MEGABYTES/GIGABYTES conversion (/1000)
    yields the same GB number the Alfa app shows. The operator labels plans in
    decimal GB even though their internal accounting uses 1024-MB; matching the
    user-visible label is the right UX call."""
    if value is None:
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
    return num


def _normalise_service(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map an Alfa ``Services/Get`` record (``AlfaServiceDTO``) to a stable
    internal shape. Field names on the right come from jadx decompile of
    AlfaNet APK v5.2.86 — see
    ``_archive/HomeLab/alfa/2026-04-26-jadx-body-shapes.md``. Returns ``None``
    when the input isn't a dict so the coordinator can drop bad records
    silently rather than crashing.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return {
            # Primary identifiers passed back to all paid ops.
            "service_id": raw.get("id"),
            "alias": raw.get("alias"),
            "name": raw.get("name"),
            # State flags.
            "is_subscribed": bool(raw.get("is_subscribed", False)),
            "is_renewable": bool(raw.get("is_renewable", False)),
            "is_managable": bool(raw.get("is_managable", False)),
            "can_subscribe": bool(raw.get("can_subscribe", False)),
            "can_unsubscribe": bool(raw.get("can_unsubscribe", False)),
            "view_only": bool(raw.get("view_only", False)),
            # PIN flow + action_name candidates for PinCode/Get.
            "require_pin": bool(raw.get("require_pin", False)),
            "sub_unsub_action_name": raw.get("sub_unsub_action_name"),
            "manage_action_name": raw.get("manage_action_name"),
            # Display + scheduling.
            "price": raw.get("price"),
            "validity": raw.get("validity"),
            "service_cycle_date": raw.get("ServiceCycleDate"),
            "short_description": raw.get("short_description"),
            # Bundle catalog (inline — Bundle/Renew & Bundle/Modify draw from here).
            "bundles": raw.get("bundles") or [],
            # Keep the raw record so future fields (or live debugging) don't
            # require redeploying the coordinator.
            "raw": raw,
        }
    except (TypeError, ValueError, AttributeError) as err:
        _LOGGER.warning("Failed to normalise Alfa service record: %s — raw=%r", err, raw)
        return None


class AlfaClient:
    """Async client for the Alfa mobile API.

    Holds the credentials and a (cached) ``accesstoken``. Re-authenticates
    transparently on token expiry; raises :class:`AlfaAuthError` only when
    the password itself is wrong.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        mobile: str,
        password: str,
    ) -> None:
        self._session = session
        self._mobile = mobile.strip()
        self._password = password
        self._token: str | None = None

    @property
    def mobile_number(self) -> str:
        return self._mobile

    async def _call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        ts = int(time.time())
        payload = {
            **body,
            "Method": method,
            "Platform": "android",
            "App_version": "5.2.86",
            "TimeStamp": ts,
            "Signature": f"{random.randint(0, 100)}{ts}",
        }
        encrypted = _encrypt(json.dumps(payload, separators=(",", ":")))
        envelope = {"Data": encrypted}
        try:
            async with self._session.post(
                API_URL,
                json=envelope,
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                if resp.status >= 500:
                    raise AlfaApiError(f"HTTP {resp.status} from Alfa: {text[:200]}")
                if resp.status >= 400:
                    raise AlfaApiError(f"HTTP {resp.status}: {text[:200]}")
                try:
                    outer = json.loads(text)
                except ValueError as err:
                    raise AlfaApiError(f"Bad JSON envelope: {err}") from err
                if isinstance(outer, dict) and outer.get("Error"):
                    raise AlfaApiError(f"Alfa API error: {outer['Error']}")
                data_blob = outer.get("Data") if isinstance(outer, dict) else None
                if not data_blob:
                    raise AlfaApiError(f"No Data field in response: {text[:200]}")
                try:
                    decrypted = _decrypt(data_blob)
                except Exception as err:  # noqa: BLE001
                    raise AlfaApiError(f"Decrypt failed: {err}") from err
                try:
                    result = json.loads(decrypted)
                except ValueError as err:
                    raise AlfaApiError(f"Bad inner JSON: {err}") from err
                _LOGGER.debug("Alfa %s -> Status=%s", method, result.get("Status"))
                return result
        except aiohttp.ClientError as err:
            raise AlfaApiError(str(err)) from err
        except asyncio.TimeoutError as err:
            raise AlfaApiError(f"Timeout calling {method}") from err

    async def _signin(self) -> None:
        result = await self._call(
            "Signin",
            {
                "Username": self._mobile,
                "UserPassword": self._password,
                "PlayerId": "",
            },
        )
        status = result.get("Status")
        token = result.get("accesstoken")
        if status in _STATUS_AUTH_FAILED or not token:
            msg = result.get("Message") or f"Status {status}"
            raise AlfaAuthError(f"Signin rejected: {msg}")
        if status not in _STATUS_OK:
            raise AlfaApiError(f"Unexpected Signin Status={status}")
        self._token = token

    async def _authed_call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._token:
            await self._signin()
        full = {**body, "AccessToken": self._token}
        result = await self._call(method, full)
        status = result.get("Status")
        if status in _STATUS_AUTH_FAILED:
            # Token expired — re-Signin once and retry.
            self._token = None
            await self._signin()
            full = {**body, "AccessToken": self._token}
            result = await self._call(method, full)
        return result

    async def _v3_call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST to V3 mobapirules with the AlfaNet-app envelope shape.

        Mirrors the OkHttp interceptor in jadx ``Z1/f.java``:
          * Splits ``path`` (e.g. ``Services/Get``) into ``Method`` and
            ``ActionID`` body fields.
          * Adds the standard envelope fields (Platform/App_version/
            TimeStamp/Signature) into the body.
          * Whole-body encrypts (same key/IV as V2 — the jadx
            ``CrossPlatformEncryptor`` Caesar-shifted constants resolve to
            V2's key + IV).
          * Sends ``application/x-www-form-urlencoded`` with a single
            ``Data=<ciphertext>`` field — NOT the JSON envelope V2 uses.

        The actual URL path is rewritten to ``/V3/Default/Get`` regardless
        of the endpoint name (the routing is encoded in ``Method`` /
        ``ActionID`` inside the encrypted body)."""
        try:
            method_part, action_part = path.split("/", 1)
        except ValueError as err:
            raise AlfaApiError(
                f"V3 path must contain a '/' splitting Method/ActionID: {path!r}"
            ) from err

        ts = int(time.time())
        payload = {
            **body,
            "Method": method_part,
            "ActionID": action_part,
            "Platform": "android",
            "App_version": "5.2.86",
            "TimeStamp": ts,
            "Signature": f"{random.randint(0, 100)}{ts}",
        }
        encrypted = _encrypt(json.dumps(payload, separators=(",", ":")))
        # JSON envelope (same shape as V2) — the WAF rejects form-encoded
        # bodies even though the OkHttp interceptor in the app builds one.
        # The app's actual on-the-wire request likely passes through F5's
        # accept list because of TLS fingerprinting; our requests don't,
        # so we use the JSON envelope which the application backend ALSO
        # accepts.
        try:
            async with self._session.post(
                V3_API_URL,
                json={"Data": encrypted},
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                if resp.status >= 500:
                    raise AlfaApiError(
                        f"HTTP {resp.status} from V3 {path}: {text[:200]}"
                    )
                if resp.status >= 400:
                    raise AlfaApiError(f"HTTP {resp.status} V3 {path}: {text[:200]}")
                try:
                    outer = json.loads(text)
                except ValueError as err:
                    raise AlfaApiError(f"Bad JSON envelope from V3 {path}: {err}") from err
                if isinstance(outer, dict) and outer.get("Error"):
                    raise AlfaApiError(f"V3 {path} API error: {outer['Error']}")
                # Per the V2 envelope, response decryption is whole-body
                # via the same AES key. The V3 interceptor (f.java line 101+)
                # decrypts via the same encryptor used for the request.
                data_blob = outer.get("Data") if isinstance(outer, dict) else None
                if not data_blob:
                    # Some V3 errors return a plain JSON envelope (no Data
                    # field). Surface the whole structure rather than masking.
                    return outer if isinstance(outer, dict) else {"raw": outer}
                try:
                    decrypted = _decrypt(data_blob)
                except Exception as err:  # noqa: BLE001
                    raise AlfaApiError(f"V3 {path} decrypt failed: {err}") from err
                try:
                    result = json.loads(decrypted)
                except ValueError as err:
                    raise AlfaApiError(f"Bad inner JSON from V3 {path}: {err}") from err
                _LOGGER.debug("Alfa V3 %s -> Status=%s", path, result.get("Status"))
                return result
        except aiohttp.ClientError as err:
            raise AlfaApiError(f"V3 transport error on {path}: {err}") from err
        except asyncio.TimeoutError as err:
            raise AlfaApiError(f"V3 timeout on {path}") from err

    async def _v3_authed_call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """V3 call with the V2 Signin ``accesstoken`` injected as the
        ``AccessToken`` body field. TedmobApi has no Signin endpoint of its
        own — all V3 ops accept a token harvested from V2 ``Signin``.

        Re-signin-on-auth-fail mirrors the V2 ``_authed_call`` retry pattern
        once V3 status-code semantics are confirmed; for now the V2 status
        code set (``_STATUS_AUTH_FAILED``) is reused — same backend operator,
        same auth subsystem in all likelihood."""
        if not self._token:
            await self._signin()
        full = {**body, "AccessToken": self._token}
        result = await self._v3_call(path, full)
        status = result.get("Status") if isinstance(result, dict) else None
        if status in _STATUS_AUTH_FAILED:
            self._token = None
            await self._signin()
            full = {**body, "AccessToken": self._token}
            result = await self._v3_call(path, full)
        return result

    async def async_validate(self) -> dict[str, Any]:
        """Verify credentials by signing in. Returns the Profile block."""
        await self._signin()
        # GetAccountDetails confirms the line is provisioned.
        details = await self._authed_call(
            "GetAccountDetails", {"MSISDN": self._mobile}
        )
        return {
            "MobileNumberValue": details.get("MobileNumberValue") or self._mobile,
            "TypeValue": details.get("TypeValue"),
            "SubTypeValue": details.get("SubTypeValue"),
        }

    async def async_get_account_data(self) -> dict[str, Any]:
        """Fetch account, expiry, and recharge history; normalise."""
        if not self._token:
            await self._signin()
        details, expiry, recharge = await asyncio.gather(
            self._authed_call("GetAccountDetails", {"MSISDN": self._mobile}),
            self._authed_call("GetPrepaidExpiryDate", {"MSISDN": self._mobile}),
            self._authed_call("GetRechargeHistory", {"MSISDN": self._mobile}),
            return_exceptions=False,
        )

        result: dict[str, Any] = {
            "mobile": details.get("MobileNumberValue") or self._mobile,
            "balance_usd": _parse_money(details.get("CurrentBalanceValue")),
            "balance_raw": details.get("CurrentBalanceValue"),
            "response_code": details.get("Status"),
            "services": [],
            "last_recharge_amount": None,
            "last_recharge_date": None,
            "recharge_history": [],
            "days_until_expiry": None,
            "data_used_mb": None,
            "data_total_mb": None,
            "data_remaining_mb": None,
            "plan_name": None,
            "validity": None,
        }

        primary_used: float | None = None
        primary_total: float | None = None
        primary_validity: date | None = None
        primary_plan: str | None = None

        for svc in details.get("ServiceInformationValue") or []:
            name = svc.get("ServiceNameValue")
            for det in svc.get("ServiceDetailsInformationValue") or []:
                used = _to_mb(det.get("ConsumptionValue"), det.get("ConsumptionUnitValue"))
                total = _to_mb(det.get("PackageValue"), det.get("PackageUnitValue"))
                validity = _parse_dmy(det.get("ValidityDateValue"))
                entry = {
                    "service": name,
                    "description": det.get("DescriptionValue"),
                    "used_mb": used,
                    "total_mb": total,
                    "remaining_mb": (
                        total - used if used is not None and total is not None else None
                    ),
                    "validity": validity.isoformat() if validity else None,
                }
                result["services"].append(entry)
                if primary_used is None and used is not None and total is not None:
                    primary_used = used
                    primary_total = total
                    primary_validity = validity
                    primary_plan = name or det.get("DescriptionValue")

        result["data_used_mb"] = primary_used
        result["data_total_mb"] = primary_total
        result["data_remaining_mb"] = (
            (primary_total - primary_used)
            if primary_used is not None and primary_total is not None
            else None
        )
        result["plan_name"] = primary_plan
        result["validity"] = (
            datetime.combine(primary_validity, datetime.min.time()).astimezone()
            if primary_validity
            else None
        )

        # Recharge history — newest first.
        history: list[dict[str, Any]] = []
        for item in recharge.get("MSISDNRecharges") or []:
            d = _parse_dmy(item.get("TimeStamp"))
            history.append({
                "date": d.isoformat() if d else None,
                "amount": _parse_money(item.get("Amount")),
                "balance_before": _parse_money(item.get("BalanceB")),
                "balance_after": _parse_money(item.get("BalanceA")),
                "account": item.get("AccountNumber"),
            })
        result["recharge_history"] = history
        if history:
            result["last_recharge_amount"] = history[0]["amount"]
            if history[0]["date"]:
                result["last_recharge_date"] = datetime.combine(
                    date.fromisoformat(history[0]["date"]), datetime.min.time()
                ).astimezone()

        # Days until expiry — derive from PrepaidExpiryDate (DD/MM/YYYY).
        exp_date = _parse_dmy(expiry.get("PrepaidExpiryDate"))
        if exp_date:
            result["days_until_expiry"] = (exp_date - date.today()).days

        return result

    async def async_get_services_list(self) -> list[dict[str, Any]]:
        """Fetch the list of services + their inline bundle catalogs from V3.

        Endpoint + body shape come from jadx of AlfaNet v5.2.86 — see
        ``_archive/HomeLab/alfa/2026-04-26-jadx-body-shapes.md``. Posts to
        ``mobapirules-live/V3/Services/Get`` with per-field encryption.
        The Retrofit signature is ``List<AlfaServiceDTO>`` but the wire
        shape may either BE the list or wrap it in a single key — we probe.
        Response strings are best-effort decrypted (see
        ``_v3_decrypt_response``); if a string isn't actually encrypted on
        the wire it passes through untouched. Returns normalised dicts.
        """
        # ``LineType`` per jadx (TedmobServicesGetBody.lineType) — Pierre's
        # account is prepaid. ``MSISDN`` is the line we're querying. All
        # other body fields are optional filters; omitting them returns the
        # full service list. The V2 ``accesstoken`` is added by
        # ``_v3_authed_call``; ``Method``/``ActionID``/envelope fields are
        # added by ``_v3_call``.
        result = await self._v3_authed_call(
            "Services/Get",
            {"MSISDN": self._mobile, "LineType": "prepaid"},
        )

        # Probe container shape: V2-style envelope wraps payloads in a
        # named field. The Retrofit response type is List<AlfaServiceDTO>
        # but the wsitranslator/mobapirules envelopes typically nest under
        # a key like ``ServicesValue``.
        records: Any = None
        if isinstance(result, list):
            records = result
        elif isinstance(result, dict):
            for key in (
                "ServicesValue",
                "Services",
                "ServicesList",
                "services",
                "Result",
                "Data",
            ):
                candidate = result.get(key)
                if isinstance(candidate, list):
                    records = candidate
                    break

        if not isinstance(records, list):
            _LOGGER.warning(
                "Alfa V3 Services/Get response not a list — keys=%s",
                list(result.keys()) if isinstance(result, dict)
                else type(result).__name__,
            )
            return []

        normalised = [n for n in (_normalise_service(r) for r in records) if n]
        _LOGGER.debug(
            "Alfa V3 Services/Get -> %d records, %d after normalise",
            len(records),
            len(normalised),
        )
        return normalised
