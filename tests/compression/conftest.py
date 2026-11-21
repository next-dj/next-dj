from collections.abc import Iterator

import pytest

from next.compression.backends import forget_codec_libraries
from next.compression.manager import compression_manager


@pytest.fixture(autouse=True)
def _fresh_codecs() -> Iterator[None]:
    """Probe the codec libraries again and rebuild the backends in every test."""
    forget_codec_libraries()
    compression_manager.reset()
    yield
    forget_codec_libraries()
    compression_manager.reset()
