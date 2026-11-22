from collections.abc import Iterator

import pytest
from django.core.signals import request_finished
from django.db import close_old_connections

from next.components import registry as registry_module
from next.components.registry import DeferredComponents
from next.pages import manager as manager_module
from next.templatetags import components as component_tags


@pytest.fixture(autouse=True)
def _own_deferred_components(monkeypatch: pytest.MonkeyPatch) -> None:
    """Track the deferred components of one bench alone.

    The flag rises for good, so a shared one would slow every page bench after it.
    """
    fresh = DeferredComponents()
    for module in (registry_module, manager_module, component_tags):
        monkeypatch.setattr(module, "deferred_components", fresh)


@pytest.fixture(autouse=True)
def _closed_without_the_database(django_db_blocker) -> Iterator[None]:
    """Close a streamed response over connections an earlier bench left open.

    The stream releases the connections its thread holds as its body ends, and the
    blocker would fail that check where a bench before this one used the database.
    """
    request_finished.disconnect(close_old_connections)
    with django_db_blocker.unblock():
        yield
    request_finished.connect(close_old_connections)
