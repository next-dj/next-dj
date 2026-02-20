import importlib
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client, RequestFactory


project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

if not settings.configured:
    settings.configure(
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
            "layouts",
        ],
        ROOT_URLCONF="config.urls",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.debug",
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                        "layouts.context_processors.site_info",
                    ],
                },
            },
        ],
        NEXT_PAGES=[
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": True,
                "OPTIONS": {
                    "PAGES_DIRS": [
                        str(project_root / "examples" / "layouts" / "root_pages"),
                    ],
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                        "layouts.context_processors.site_info",
                    ],
                },
            },
        ],
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver"],
        STATIC_URL="static/",
        STATICFILES_DIRS=[str(project_root / "examples" / "layouts" / "static")],
    )

    django.setup()


@pytest.fixture()
def client():
    """Django test client fixture."""
    return Client()


@pytest.fixture()
def request_factory():
    """Django request factory fixture."""
    return RequestFactory()


@pytest.fixture()
def sample_request(request_factory):
    """Sample request fixture."""
    return request_factory.get("/")


@pytest.fixture()
def page_modules():
    """Fixture that imports all page modules (after django.setup())."""
    main_page = importlib.import_module("layouts.pages.page")
    return {
        "main_page": main_page,
    }
