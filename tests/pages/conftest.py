from collections.abc import Generator
from pathlib import Path

import pytest

from next.deps import provider_registry
from next.pages.signals import (
    context_registered,
    metadata_registered,
    page_rendered,
    template_loaded,
)
from next.testing import SignalRecorder, capture_signals
from tests.support import restored_provider_registry


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


@pytest.fixture()
def capture_metadata_registered() -> Generator[SignalRecorder, None, None]:
    """Record ``metadata_registered`` emissions."""
    with capture_signals(metadata_registered) as recorder:
        yield recorder


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Return an empty `pages` directory, the root of a page tree under test."""
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


@pytest.fixture()
def framework_providers() -> Generator[None, None, None]:
    """Keep only the providers the framework ships, dropping the ones tests declared.

    A provider a test declares stays registered, and a context graph reads it as unknown.
    """
    with restored_provider_registry():
        provider_registry.replace(
            [cls for cls in provider_registry if cls.__module__.startswith("next.")]
        )
        yield
