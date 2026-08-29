from collections.abc import Generator

import pytest

from next.pages.signals import context_registered, page_rendered, template_loaded
from next.testing import SignalRecorder, capture_signals


@pytest.fixture()
def capture_template_loaded() -> Generator[SignalRecorder, None, None]:
    """Record ``template_loaded`` emissions."""
    with capture_signals(template_loaded) as recorder:
        yield recorder


@pytest.fixture()
def capture_context_registered() -> Generator[SignalRecorder, None, None]:
    """Record ``context_registered`` emissions."""
    with capture_signals(context_registered) as recorder:
        yield recorder


@pytest.fixture()
def capture_page_rendered() -> Generator[SignalRecorder, None, None]:
    """Record ``page_rendered`` emissions."""
    with capture_signals(page_rendered) as recorder:
        yield recorder
