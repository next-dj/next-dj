from collections.abc import Iterator

import pytest

from next.defer.policy import StreamSlots
from next.pages import page
from tests.support import hosts, rules
from tests.support.streams import isolated_slots


@pytest.fixture()
def slots(monkeypatch: pytest.MonkeyPatch) -> StreamSlots:
    """Count the streams of this test alone, apart from any another test left open."""
    return isolated_slots(monkeypatch)


@pytest.fixture(autouse=True)
def _quiet_hosts() -> None:
    """Start every test with no call recorded by the component and page sources."""
    hosts.NOTES.clear()
    hosts.RUNS.clear()
    rules.KINDS_SEEN.clear()


@pytest.fixture(autouse=True)
def _fresh_compositions() -> Iterator[None]:
    """Start and end each test with no template composed for any page."""
    page._templates.composed.clear()
    page._templates.composed_sources.clear()
    yield
    page._templates.composed.clear()
    page._templates.composed_sources.clear()
