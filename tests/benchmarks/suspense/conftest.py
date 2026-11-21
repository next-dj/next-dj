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
def _closed_without_the_database() -> Iterator[None]:
    """Close a streamed response without touching a connection the bench cannot read."""
    request_finished.disconnect(close_old_connections)
    try:
        yield
    finally:
        request_finished.connect(close_old_connections)
