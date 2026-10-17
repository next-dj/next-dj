import subprocess
import sys

import pytest

import next.boundaries as boundaries_package
from next.boundaries import _LAZY_ATTRIBUTES


class TestPackageSurface:
    """The package names its whole surface and loads the scan engine on first use."""

    def test_all_matches_dir(self) -> None:
        assert dir(boundaries_package) == sorted(boundaries_package.__all__)

    @pytest.mark.parametrize("name", sorted(_LAZY_ATTRIBUTES))
    def test_every_lazy_name_resolves(self, name: str) -> None:
        assert getattr(boundaries_package, name) is not None
        assert name in boundaries_package.__all__

    def test_an_unknown_name_raises(self) -> None:
        with pytest.raises(AttributeError, match="has no attribute 'missing'"):
            _ = boundaries_package.missing

    def test_the_aggregate_signals_load_no_scan_engine(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys, next.signals\n"
                    "loaded = sorted(m for m in sys.modules "
                    "if m.startswith('next.boundaries') or 'grimp' in m)\n"
                    "print(','.join(loaded))\n"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == (
            "next.boundaries,next.boundaries.errors,next.boundaries.signals"
        )
