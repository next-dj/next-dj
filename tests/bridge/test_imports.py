import json
import subprocess
import sys

import pytest

from tests.django_setup import PROJECT_ROOT
from tests.support.imports import module_names
from tests.test_layers import CORE, FEATURES, area_of


def _probe(names: list[str]) -> dict[str, object]:
    """Import `names` in a fresh interpreter, run async code from sync code there."""
    completed = subprocess.run(
        [sys.executable, "-m", "tests.bridge.probe"],
        cwd=PROJECT_ROOT,
        input=json.dumps(names),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def probed() -> dict[str, object]:
    kernel = [name for name in module_names() if area_of(name) in CORE]
    return _probe(kernel)


class TestKernelRuntime:
    """The kernel runs async code from sync code without loading a feature area."""

    def test_every_bridge_answers_on_the_calling_thread(self, probed) -> None:
        assert probed["answered"] == {"drive": True, "bridge": True, "scope": True}

    def test_no_feature_area_is_loaded(self, probed) -> None:
        loaded = probed["loaded"]
        assert isinstance(loaded, list)
        assert "next.bridge.driver" in loaded
        assert sorted(name for name in loaded if area_of(name) in FEATURES) == []
