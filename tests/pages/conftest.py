from collections.abc import Generator
from typing import Any

import pytest

from next.pages.signals import context_registered, page_rendered, template_loaded


@pytest.fixture()
def capture_template_loaded() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``template_loaded`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append({"sender": sender, **kwargs})

    template_loaded.connect(_listener)
    try:
        yield events
    finally:
        template_loaded.disconnect(_listener)


@pytest.fixture()
def capture_context_registered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``context_registered`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append({"sender": sender, **kwargs})

    context_registered.connect(_listener)
    try:
        yield events
    finally:
        context_registered.disconnect(_listener)


@pytest.fixture()
def capture_page_rendered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``page_rendered`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append({"sender": sender, **kwargs})

    page_rendered.connect(_listener)
    try:
        yield events
    finally:
        page_rendered.disconnect(_listener)
