import importlib.util
import sys
from pathlib import Path

import django
import pytest
from django.apps import apps
from django.conf import settings
from django.test import Client


forms_example_root = Path(__file__).resolve().parent.parent
repo_root = forms_example_root.parent.parent
sys.path.insert(0, str(forms_example_root))
sys.path.insert(0, str(repo_root))

if not settings.configured:
    settings.configure(
        SECRET_KEY="test-secret-key-for-forms-example",
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            },
        },
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "next",
            "todos",
        ],
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        ROOT_URLCONF="config.urls",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ],
                },
            },
        ],
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver"],
    )

if not apps.ready:
    django.setup()

_FormsPagesLoaded = {"done": False}


def _eager_load_todo_pages() -> None:
    """Load page modules so ``@forms.action`` handlers register on ``form_action_manager``."""
    if _FormsPagesLoaded["done"]:
        return
    for rel, mod_name in (
        ("todos/pages/page.py", "todos_pages_root"),
        (
            "todos/pages/edit/[int:id]/page.py",
            "todos_pages_edit_bracket",
        ),
    ):
        path = forms_example_root / rel
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    _FormsPagesLoaded["done"] = True


_eager_load_todo_pages()


@pytest.fixture()
def client():
    """Django test client fixture."""
    return Client()
