import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client, RequestFactory

# add project root to python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# configure django settings for layouts example
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
                    "PAGES_DIR": str(
                        project_root / "examples" / "layouts" / "layouts" / "pages"
                    ),
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
    )

    # setup django
    django.setup()


@pytest.fixture
def client():
    """django test client fixture."""
    return Client()


@pytest.fixture
def request_factory():
    """django request factory fixture."""
    return RequestFactory()


@pytest.fixture
def sample_request(request_factory):
    """sample request fixture."""
    return request_factory.get("/")


@pytest.fixture
def page_modules():
    """fixture that imports all page modules."""
    import layouts.pages.page as main_page

    return {
        "main_page": main_page,
    }
