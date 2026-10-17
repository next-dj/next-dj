from __future__ import annotations

import pytest

from next.boundaries.manager import boundaries_manager
from tests.benchmarks.factories import build_boundary_apps
from tests.support.boundaries import boundary_project


_APPS = 50


@pytest.fixture()
def ring_project(tmp_path):
    with boundary_project(tmp_path, build_boundary_apps(_APPS)):
        yield


@pytest.mark.usefixtures("ring_project")
class TestBenchBoundariesScan:
    """The whole scan of a 50-app ring, every edge kind between neighbours."""

    @pytest.mark.benchmark(group="boundaries.scan")
    def test_graph_and_findings(self, benchmark) -> None:
        scan = benchmark.pedantic(
            boundaries_manager.scan,
            setup=boundaries_manager.reset,
            rounds=5,
            iterations=1,
        )

        kinds = {edge.kind for edge in scan.graph.edges}
        assert kinds == {
            "import",
            "fk",
            "get_model",
            "signal",
            "di",
            "zone",
            "component",
            "perm",
        }
        assert len(scan.graph.tangles()) == 1
        assert len(scan.graph.apps) == _APPS
