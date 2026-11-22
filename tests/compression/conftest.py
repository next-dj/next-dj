from collections.abc import Iterator

import pytest

from next.compression.backends import forget_codec_libraries
from next.compression.manager import compression_manager
from tests.support.compressors import install


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker for tests that drive the codec libraries of the machine."""
    config.addinivalue_line(
        "markers", "real_codecs: keep the installed codec libraries"
    )


@pytest.fixture(autouse=True)
def _fresh_codecs() -> Iterator[None]:
    """Probe the codec libraries again and rebuild the backends in every test."""
    forget_codec_libraries()
    compression_manager.reset()
    yield
    forget_codec_libraries()
    compression_manager.reset()


@pytest.fixture(autouse=True)
def no_codec_libraries(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, _fresh_codecs: None
) -> None:
    """Leave gzip the only codec unless the test is marked `real_codecs`.

    A test of another codec calls `install` with its fake modules, which replaces this.
    """
    if request.node.get_closest_marker("real_codecs") is None:
        install(monkeypatch)
