from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client


example_root = Path(__file__).resolve().parent.parent
repo_root = example_root.parent.parent
sys.path.insert(0, str(example_root))
sys.path.insert(0, str(repo_root))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if not settings.configured:
    django.setup()

_ExamplePagesLoadedState = {"done": False}


def _eager_load_example_pages() -> None:
    """Import page modules so routes, bracket pages, and form actions are registered."""
    if _ExamplePagesLoadedState["done"]:
        return

    authors_page = example_root / "myapp" / "pages" / "authors" / "[int:id]" / "page.py"
    spec = importlib.util.spec_from_file_location("myapp_authors_page", authors_page)
    authors_mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(authors_mod)

    for sub in ("edit", "details"):
        path = example_root / "myapp" / "pages" / "posts" / "[int:id]" / sub / "page.py"
        name = f"myapp_posts_bracket_{sub}"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

    for rel, mod_name in (
        (
            "myapp/pages/account/login/page.py",
            "myapp_account_login_page",
        ),
        (
            "myapp/pages/account/register/page.py",
            "myapp_account_register_page",
        ),
        (
            "myapp/pages/account/profile/page.py",
            "myapp_account_profile_page",
        ),
        (
            "myapp/pages/posts/create/page.py",
            "myapp_posts_create_page",
        ),
    ):
        path = example_root / rel
        spec = importlib.util.spec_from_file_location(mod_name, path)
        extra_mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(extra_mod)

    _ExamplePagesLoadedState["done"] = True


_eager_load_example_pages()


@pytest.fixture()
def client() -> Client:
    """Django test client fixture."""
    return Client()


@pytest.fixture()
def user_factory():
    """Create users with less boilerplate (use with ``@pytest.mark.django_db``)."""
    user_model = get_user_model()

    def _create(**kwargs):
        password = kwargs.pop("password", "pw")
        return user_model.objects.create_user(password=password, **kwargs)

    return _create
