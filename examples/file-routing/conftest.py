import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client

# add project root to python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# configure django settings for file-routing example
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "next",
            "myapp",
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
                    ],
                },
            },
        ],
        NEXT_PAGES=[
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": True,
            },
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {
                    "PAGES_DIR": str(
                        project_root / "examples" / "file-routing" / "root_pages"
                    ),
                },
            },
        ],
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver"],
    )

    # setup django
    django.setup()


@pytest.fixture
def client():
    """django test client fixture."""
    return Client()
