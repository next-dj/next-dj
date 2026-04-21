from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from next.pages import Page
from next.pages.loaders import DjxTemplateLoader, LayoutManager, PythonTemplateLoader
from next.pages.registry import PageContextRegistry
from next.pages.signals import context_registered, page_rendered, template_loaded
from next.urls import URLPatternParser
from tests.support import named_temp_py


@pytest.fixture()
def page_instance():
    """Create a fresh Page instance for each test."""
    return Page()


@pytest.fixture()
def url_parser():
    """Create a URLPatternParser instance for testing."""
    return URLPatternParser()


@pytest.fixture()
def python_template_loader():
    """Create a PythonTemplateLoader instance for testing."""
    return PythonTemplateLoader()


@pytest.fixture()
def djx_template_loader():
    """Create a DjxTemplateLoader instance for testing."""
    return DjxTemplateLoader()


@pytest.fixture()
def context_manager():
    """Create a PageContextRegistry instance for testing."""
    return PageContextRegistry(None)


@pytest.fixture()
def layout_manager():
    """Create a LayoutManager instance for testing."""
    return LayoutManager()


@pytest.fixture()
def mock_frame():
    """Mock inspect.currentframe for testing."""
    with patch("next.pages.manager.inspect.currentframe") as mock_frame:
        yield mock_frame


@pytest.fixture()
def test_file_path():
    """Create a test file path for render tests."""
    return Path("/test/path/page.py")


@pytest.fixture()
def global_file_path():
    """Create a file path for global page tests."""
    return Path("/test/global/page.py")


@pytest.fixture()
def temp_python_file():
    """Create a temporary Python file for testing."""
    with named_temp_py('template = "test template"') as path:
        yield path


@pytest.fixture()
def context_temp_file():
    """Create a temporary file for context decorator tests."""
    with named_temp_py("def test_func(): pass") as path:
        yield path


@pytest.fixture()
def capture_template_loaded() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``template_loaded`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
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

    def _listener(sender: object, **kwargs: object) -> None:
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

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    page_rendered.connect(_listener)
    try:
        yield events
    finally:
        page_rendered.disconnect(_listener)
