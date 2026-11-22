from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from next.deps import provider_registry
from next.pages.compression import compress_store
from next.pages.defer import page_defer_store
from next.pages.loaders import forget_layout_texts
from next.pages.router import router_store
from next.pages.signals import (
    context_registered,
    metadata_registered,
    page_rendered,
    template_loaded,
)
from next.pages.transactions import atomic_store
from next.testing import SignalRecorder, capture_signals
from tests.support import restored_provider_registry, rewrite_in_tick
from tests.support.pages import write_page


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


@pytest.fixture()
def fresh_declarations() -> Generator[None, None, None]:
    """Start and end the test with no `page` declaration of any kind."""
    stores = (compress_store, page_defer_store, router_store, atomic_store)
    for store in stores:
        store.reset()
    yield
    for store in stores:
        store.reset()


@pytest.fixture()
def page_declaring() -> Callable[[Path, str], Path]:
    """Return a writer of a `page.py` that runs a declaration and serves a template."""

    def write(directory: Path, declaration: str) -> Path:
        return write_page(
            directory,
            source=f"from next.pages import page\n\n{declaration}\n\ntemplate = '<p>x</p>'\n",
        )

    return write


@pytest.fixture()
def error_file(tmp_path: Path) -> Path:
    """Write an `error.djx` that shows its frame, its kind and any leaked key."""
    path = tmp_path / "error.djx"
    rewrite_in_tick(path, "{{ frame }}|{{ error.kind }}|{{ leaked|default:'clean' }}")
    forget_layout_texts()
    return path


@pytest.fixture()
def loading_file(tmp_path: Path) -> Path:
    """Write a `loading.djx` that shows its frame."""
    path = tmp_path / "loading.djx"
    rewrite_in_tick(path, "<i>wait {{ frame|default:'' }}</i>")
    forget_layout_texts()
    return path
