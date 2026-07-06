"""Unit test for the anti-forgery token scraper (no network)."""
from __future__ import annotations

from pathlib import Path


def test_scrape_token_extracts_value():
    # api.py imports aiohttp; skip cleanly if not installed in this env.
    import importlib.util
    if importlib.util.find_spec("aiohttp") is None:
        import pytest
        pytest.skip("aiohttp not installed in test env")

    # api.py uses package-relative imports (`from . import parsers`), so it
    # can't load as a bare top-level module the way parsers.py/const.py do
    # (those have zero relative imports). It also can't be imported via the
    # real `custom_components.alfa_lb` package, because that package's
    # __init__.py imports `homeassistant` (not installed here) and the old
    # AlfaClient name pending a later task. So we register a throwaway stand-in
    # package pointing at the alfa_lb directory, purely so Python's import
    # machinery can resolve api.py's `.`-relative imports of parsers/const.
    import sys
    import types

    alfa_lb_dir = Path(__file__).resolve().parents[1] / "custom_components" / "alfa_lb"
    pkg_name = "_alfa_lb_api_test_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(alfa_lb_dir)]
        sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(f"{pkg_name}.api", alfa_lb_dir / "api.py")
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)

    html = (
        '<form method="post">'
        '<input name="__RequestVerificationToken" type="hidden" '
        'value="CfDJ8ABC-123_xyz.TOKEN" />'
        '<input name="Username" /></form>'
    )
    assert api._scrape_token(html) == "CfDJ8ABC-123_xyz.TOKEN"
    assert api._scrape_token("<html>no token here</html>") is None
