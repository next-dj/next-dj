from unittest.mock import Mock, patch

import pytest

from next.urls import FileRouterBackend, RouterBackend, RouterFactory, RouterManager
from tests.support import named_temp_py


@pytest.fixture(autouse=True)
def _pristine_router_registry():
    """Restore the process-global RouterFactory registry after each test.

    Tests register backend classes into a ClassVar dict, so without a
    snapshot the keys leak and poison run order.
    """
    snapshot = dict(RouterFactory._backends)
    yield
    RouterFactory._backends.clear()
    RouterFactory._backends.update(snapshot)


@pytest.fixture()
def router():
    """Fresh FileRouterBackend instance."""
    return FileRouterBackend()


@pytest.fixture()
def mock_settings():
    """Patch the ``settings`` object ``resolve_base_dir`` reads.

    ``DEBUG`` starts off, because a bare ``Mock`` attribute reads as truthy and
    would put every router that consults it on the disk-watching path.
    """
    mock = Mock()
    mock.DEBUG = False
    with patch("next.utils.settings", mock):
        yield mock


@pytest.fixture()
def temp_file():
    """Temporary ``page.py`` with a minimal render function."""
    with named_temp_py("def render(request, **kwargs):\n    return 'response'") as path:
        yield path


@pytest.fixture()
def custom_backend_class():
    """Minimal concrete RouterBackend for registration tests."""

    class CustomBackend(RouterBackend):
        def generate_urls(self):
            return []

    return CustomBackend


@pytest.fixture()
def manager():
    """Fresh RouterManager."""
    return RouterManager()
