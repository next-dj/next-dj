import pytest

from next.urls import FileRouterBackend, RouterBackend
from tests.support import named_temp_py


@pytest.fixture()
def router():
    """Fresh FileRouterBackend instance."""
    return FileRouterBackend()


@pytest.fixture()
def mock_settings():
    """Patch ``settings`` in both ``urls`` and ``filesystem`` (``resolve_base_dir``)."""
    from unittest.mock import Mock, patch

    mock = Mock()
    with (
        patch("next.urls.backends.settings", mock),
        patch("next.utils.settings", mock),
    ):
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
    from next.urls import RouterManager

    return RouterManager()
