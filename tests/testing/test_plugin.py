from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from next.testing import NextClient, plugin
from next.testing.plugin import CACHE_INI, COMPONENTS_INI, PAGES_INI


class _RecordingParser:
    """Stand-in for `pytest.Parser` recording every `addini` registration."""

    def __init__(self) -> None:
        self.inis: dict[str, dict[str, object]] = {}

    def addini(self, name, description, **options) -> None:
        self.inis[name] = {"description": description, **options}


def _config(**values) -> SimpleNamespace:
    """Build a config stub answering `getini` from the given ini values."""
    defaults = {PAGES_INI: [], COMPONENTS_INI: False, CACHE_INI: False}
    return SimpleNamespace(getini=lambda name: {**defaults, **values}[name])


class TestAddOption:
    """The plugin registers its ini options with usable types and defaults."""

    def test_registers_the_three_ini_options(self) -> None:
        """Page dirs are paths, the component and cache switches are booleans."""
        parser = _RecordingParser()
        plugin.pytest_addoption(parser)
        assert parser.inis[PAGES_INI]["type"] == "paths"
        assert parser.inis[PAGES_INI]["default"] == []
        assert parser.inis[COMPONENTS_INI]["type"] == "bool"
        assert parser.inis[COMPONENTS_INI]["default"] is False
        assert parser.inis[CACHE_INI]["type"] == "bool"
        assert parser.inis[CACHE_INI]["default"] is False


class TestNextPages:
    """The session fixture imports exactly what the ini file asks for."""

    def test_loads_every_configured_directory(self) -> None:
        """Each `next_pages` entry is handed to the eager loader in order."""
        first, second = Path("/pages/one"), Path("/pages/two")
        with patch("next.testing.plugin.eager_load_pages") as mock_load:
            plugin.next_pages.__wrapped__(_config(**{PAGES_INI: [first, second]}))
        assert [call.args[0] for call in mock_load.call_args_list] == [first, second]

    @pytest.mark.parametrize(
        ("enabled", "calls"), [(False, 0), (True, 1)], ids=["off", "on"]
    )
    def test_components_load_only_when_asked(
        self, calls: int, *, enabled: bool
    ) -> None:
        """`next_components` gates the component import, and defaults to off."""
        with (
            patch("next.testing.plugin.eager_load_pages"),
            patch("next.testing.plugin.eager_load_components") as mock_components,
        ):
            plugin.next_pages.__wrapped__(_config(**{COMPONENTS_INI: enabled}))
        assert mock_components.call_count == calls


class TestCacheIsolation:
    """Cache clearing stays opt-in so an unrelated suite keeps its warm cache."""

    @pytest.mark.parametrize(
        ("enabled", "calls"), [(False, 0), (True, 1)], ids=["off", "on"]
    )
    def test_cache_clears_only_when_asked(self, calls: int, *, enabled: bool) -> None:
        """`next_clear_cache` gates the clear, so an unrelated suite stays warm."""
        with patch("next.testing.plugin.cache") as mock_cache:
            plugin.next_cache_isolation.__wrapped__(_config(**{CACHE_INI: enabled}))
        assert mock_cache.clear.call_count == calls


class TestNextClient:
    """The client fixture hands each test its own framework client."""

    def test_builds_a_client(self) -> None:
        """The fixture body returns a framework client."""
        assert isinstance(plugin.next_client.__wrapped__(), NextClient)

    def test_builds_a_fresh_client_per_test(self) -> None:
        """Two resolutions never share cookies or a login."""
        assert plugin.next_client.__wrapped__() is not plugin.next_client.__wrapped__()
