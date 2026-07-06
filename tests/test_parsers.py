"""Unit tests for alfa_lb pure parsers (no HA, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

# Import the parsers module directly without importing the HA package
# (which would pull in aiohttp/homeassistant). parsers.py has no such deps.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "alfa_lb"))

import parsers  # noqa: E402
from conftest import load_json, load_text  # noqa: E402


def test_parse_money_dollar_string():
    assert parsers.parse_money("$ 19.41") == 19.41


def test_parse_money_bare_number():
    assert parsers.parse_money(59) == 59.0


def test_parse_money_empty_is_none():
    assert parsers.parse_money("") is None
    assert parsers.parse_money(None) is None


def test_parse_portal_dt_full():
    dt = parsers.parse_portal_dt("20260804235959")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (
        2026, 8, 4, 23, 59, 59,
    )
    assert dt.tzinfo is not None


def test_parse_portal_dt_bad_input():
    assert parsers.parse_portal_dt("") is None
    assert parsers.parse_portal_dt(None) is None
    assert parsers.parse_portal_dt("nope") is None


def test_parse_consumption_two_bundles():
    data = parsers.parse_consumption(load_json("getconsumptionasync.json"))
    assert data["balance_usd"] == 19.41
    assert data["mobile"] == "81265133"
    assert data["type"] == "Prepaid"
    assert len(data["bundles"]) == 2

    b300 = data["bundles"][0]
    assert b300["name"] == "Alfanet 300GB"
    assert b300["total_gb"] == 300.0
    assert b300["used_gb"] == 300.0
    assert b300["remaining_gb"] == 0.0
    # ExpiryTime 20260727235959
    assert b300["expiry"].startswith("2026-07-27")

    b400 = data["bundles"][1]
    assert b400["remaining_gb"] == 398.5

    # aggregates (decimal MB)
    assert data["data_total_mb"] == 700000
    assert data["data_used_mb"] == 301500
    assert data["data_remaining_mb"] == 398500


def test_parse_consumption_empty_payload():
    data = parsers.parse_consumption({})
    assert data["bundles"] == []
    assert data["data_remaining_mb"] is None
    assert data["balance_usd"] is None


def test_parse_consumption_excludes_non_data_and_partial_from_aggregate():
    # Synthetic payload: one valid data bundle, one non-data (voice) bundle,
    # and one data bundle with missing amounts. Only the first must count
    # toward the data aggregate; all three must still appear in bundles[].
    payload = {
        "MobileNumberValue": "81265133",
        "TypeValue": "Prepaid",
        "CurrentBalanceValue": "$ 5.00",
        "FreeUnitsValue": [
            {"DisplayName": "Data 100GB", "UsageType": "data",
             "TotalAmount": "100.00", "TotalUnit": "GB",
             "UsedAmount": "40.00", "UsedUnit": "GB",
             "ValidityDate": "20260801000000", "ExpiryTime": "20260831235959",
             "LinkedOfferName": "Prepaid Data 100GB"},
            {"DisplayName": "Voice 500min", "UsageType": "voice",
             "TotalAmount": "500.00", "TotalUnit": "MIN",
             "UsedAmount": "120.00", "UsedUnit": "MIN"},
            {"DisplayName": "Data pending", "UsageType": "data",
             "TotalAmount": "", "TotalUnit": "GB",
             "UsedAmount": "", "UsedUnit": "GB"},
        ],
    }
    data = parsers.parse_consumption(payload)
    assert len(data["bundles"]) == 3
    names = [b["name"] for b in data["bundles"]]
    assert "Voice 500min" in names
    assert "Data pending" in names
    # aggregates count ONLY the single valid data bundle (100 GB total, 40 used)
    assert data["data_total_mb"] == 100000
    assert data["data_used_mb"] == 40000
    assert data["data_remaining_mb"] == 60000


def test_parse_services_active_and_catalog():
    svc = parsers.parse_services(load_json("getmyservicesasync.json"))
    assert svc["active_bundle"]["value"] == "ALFANET400GB"
    assert svc["active_bundle"]["gb"] == 400
    assert svc["active_bundle"]["price_usd"] == 59.0
    assert svc["active_bundle"]["is_addon"] is False
    assert svc["simultaneous_activation"] is True

    assert len(svc["catalog"]) == 20
    mains = [b for b in svc["catalog"] if not b["is_addon"]]
    addons = [b for b in svc["catalog"] if b["is_addon"]]
    assert len(mains) == 10
    assert len(addons) == 10
    # 600GB main bundle present at $69
    b600 = next(b for b in mains if b["gb"] == 600)
    assert b600["price_usd"] == 69.0
    assert b600["value"] == "ALFANET600GB"


def test_parse_services_empty():
    svc = parsers.parse_services([])
    assert svc["active_bundle"] is None
    assert svc["catalog"] == []
    assert svc["simultaneous_activation"] is False
