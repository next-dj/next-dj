import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client


# add project root to python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# configure django settings for tests
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            },
        },
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                    ],
                },
            },
        ],
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "next",
        ],
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        ROOT_URLCONF="next.urls",
        SECRET_KEY="test-secret-key",  # noqa: S106
        USE_TZ=True,
        TIME_ZONE="UTC",
        NEXT_PAGES=[
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": False,
                "OPTIONS": {"PAGES_DIR": str(project_root / "tests" / "pages")},
            },
        ],
    )
    # Register form actions from test_forms before URLconf is loaded (django.setup()
    # loads next.urls and builds urlpatterns; actions must be in form_action_manager
    # by then so that the form_action URL pattern is included).
    import tests.test_forms  # noqa: F401

    django.setup()


@pytest.fixture()
def client():
    """Django test client for HTTP requests."""
    return Client()
