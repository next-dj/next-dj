import pkgutil
from importlib import import_module
from pathlib import Path

import pytest
from django.dispatch import Signal

import next as next_package
import next.signals as aggregate
from next.apps.areas import APPS, MANIFESTS, ROOT
from next.lifecycle import lifecycle
from next.manifest import Manifest


_ROOT = Path(next_package.__file__).parent
_AREAS = sorted(info.name for info in pkgutil.iter_modules([str(_ROOT)]) if info.ispkg)


def _manifest_of(area: str) -> Manifest | None:
    if not (_ROOT / area / "manifest.py").exists():
        return None
    return import_module(f"next.{area}.manifest").manifest


class TestManifests:
    """Every area that wires something declares it once, and the list walks them all."""

    def test_names_are_unique(self) -> None:
        names = [manifest.name for manifest in MANIFESTS]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("area", _AREAS)
    def test_every_area_manifest_is_walked_under_its_name(self, area: str) -> None:
        manifest = _manifest_of(area)
        if manifest is None:
            pytest.skip(f"next.{area} wires nothing at startup")
        assert manifest.name == area
        assert manifest in MANIFESTS

    def test_the_settings_reload_comes_first_and_the_app_hooks_last(self) -> None:
        assert MANIFESTS[0].name == "conf"
        assert MANIFESTS[1] is ROOT
        assert MANIFESTS[-1] is APPS

    def test_every_area_signals_module_is_named(self) -> None:
        named = {manifest.signals for manifest in MANIFESTS if manifest.signals}
        expected = {
            import_module(f"next.{area}.signals")
            for area in _AREAS
            if (_ROOT / area / "signals.py").exists()
        }
        assert named == expected

    def test_the_aggregate_re_exports_every_named_signal(self) -> None:
        names = {
            name
            for manifest in MANIFESTS
            if manifest.signals
            for name, value in vars(manifest.signals).items()
            if isinstance(value, Signal)
        }
        assert set(aggregate.__all__) == names


class TestParticipants:
    """The participants register in the order of the manifests that name them."""

    def test_the_registry_follows_the_manifests(self) -> None:
        named = [
            participant.name
            for manifest in MANIFESTS
            for participant in manifest.participants
        ]
        assert [participant.name for participant in lifecycle] == named
        assert named == ["transactions", "partial", "defer", "router"]
