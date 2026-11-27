import asyncio
from collections.abc import Iterator

import pytest

from next.compression.backends import forget_codec_libraries
from next.compression.manager import compression_manager


@pytest.fixture(autouse=True)
def _fresh_codecs() -> Iterator[None]:
    """Probe the codec libraries of the machine and build the backends per bench."""
    forget_codec_libraries()
    compression_manager.reset()
    yield
    compression_manager.reset()


@pytest.fixture()
def bench_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Own one event loop a bench drives with `run_until_complete` between rounds."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.run_until_complete(loop.shutdown_default_executor())
    loop.close()
