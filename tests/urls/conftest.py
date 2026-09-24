from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from next.testing import override_next_settings
from next.urls import RouterBackend, RouterManager
from tests.support import default_page_router_config, file_router, named_temp_py


PAGE_TREE_ROUTES: tuple[str, ...] = (
    "",
    "home",
    "login",
    "blog/2024/post",
    "items/[int:id]",
    "tag/[slug:tag]",
    "u/[uuid:uid]",
    "name/[username]",
    "files/[[rest]]",
    "docs/[[chapter]]/end",
    "articles/latest",
    "articles/[slug:topic]",
    "num/[int:x]",
    "num/[str:x]",
    "admin/[str:app_label]/[str:model_name]/[int:pk]/change",
)


def write_page(tree: Path, route: str) -> None:
    """Write a routed ``page.py`` carrying a body at `route` under `tree`."""
    directory = tree / route
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "page.py").write_text('template = "ok"\n')


@pytest.fixture()
def page_tree(tmp_path: Path):
    """Real page tree on disk wired as the only PAGE_BACKENDS source."""
    for route in PAGE_TREE_ROUTES:
        write_page(tmp_path, route)
    with override_next_settings(PAGE_BACKENDS=default_page_router_config(tmp_path)):
        yield tmp_path


@pytest.fixture()
def router():
    """Fresh FileRouterBackend instance."""
    return file_router()


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
