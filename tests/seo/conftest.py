from collections.abc import Generator

import pytest

from next.seo.signals import sitemap_items_registered
from next.testing import SignalRecorder, capture_signals


@pytest.fixture()
def capture_sitemap_items_registered() -> Generator[SignalRecorder, None, None]:
    """Record ``sitemap_items_registered`` emissions."""
    with capture_signals(sitemap_items_registered) as recorder:
        yield recorder
