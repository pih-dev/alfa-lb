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
