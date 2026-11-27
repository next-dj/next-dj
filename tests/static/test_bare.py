import json
import subprocess
import sys

import pytest

from tests.django_setup import PROJECT_ROOT
from tests.test_layers import FEATURES, area_of


@pytest.fixture(scope="module")
def probed() -> dict[str, object]:
    """Inject a page in a fresh interpreter that never readies the app."""
    completed = subprocess.run(
        [sys.executable, "-m", "tests.static.probe"],
        cwd=PROJECT_ROOT,
        input="",
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


class TestKernelInjection:
    """A page injects with the kernel alone, no port bound and no feature loaded."""

    def test_the_runtime_and_the_init_payload_land(self, probed) -> None:
        html = probed["html"]
        assert isinstance(html, str)
        assert '<script src="/static/next/next.min.js"></script>' in html
        payload = html.split("Next._init(", 1)[1].split(");</script>", 1)[0]
        assert list(json.loads(payload)) == ["$chunks"]

    def test_nothing_is_bound_and_no_provider_is_listed(self, probed) -> None:
        assert probed["bound"] == []
        assert probed["providers"] == []

    def test_no_feature_area_is_loaded(self, probed) -> None:
        loaded = probed["loaded"]
        assert isinstance(loaded, list)
        assert "next.static.inject" in loaded
        assert sorted(name for name in loaded if area_of(name) in FEATURES) == []
