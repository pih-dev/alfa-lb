"""The file-reader transport must feed the existing parsers from latest.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text("utf-8"))


def test_build_account_model_from_fixtures():
    import importlib.util
    if importlib.util.find_spec("homeassistant") is None:
        import pytest
        pytest.skip("homeassistant not installed; build_account_model needs const import")

    # Insert path AFTER skip check so homeassistant can be found
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from custom_components.alfa_lb import api  # noqa: E402

    consumption = _load("getconsumptionasync.json")
    services = _load("getmyservicesasync.json")
    model = api.build_account_model(consumption, services, 30, {"Amount": 0, "Date": ""}, 89500)

    # Aggregate remaining across the two data bundles = 398.5 GB (398500 MB).
    assert model["data_remaining_mb"] == 398500
    assert model["data_total_mb"] == 700000
    assert model["balance_usd"] == 19.41
    assert len(model["bundles"]) == 2
    assert model["active_bundle_name"] == "400GB"
    assert model["days_until_expiry"] == 30
    assert model["exchange_rate_lbp"] == 89500
