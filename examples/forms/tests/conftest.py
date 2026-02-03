import sys
from pathlib import Path

import django
import pytest
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
        NEXT_PAGES=[
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": True,
            },
        ],
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver"],
    )
    django.setup()

    try:
        import importlib.util
        from pathlib import Path

        import todos.pages.home.page  # noqa: F401

        edit_path = (
            Path(forms_example_root)
            / "todos"
            / "pages"
            / "edit"
            / "[id:int]"
            / "page.py"
        )
        if edit_path.exists():
            spec = importlib.util.spec_from_file_location("edit_page", edit_path)
            edit_page = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(edit_page)
    except ImportError:
        pass  # Modules may not exist in all test environments


@pytest.fixture()
def client():
    """Django test client fixture."""
    return Client()
