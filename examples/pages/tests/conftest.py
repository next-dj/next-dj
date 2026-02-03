import importlib
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client, RequestFactory


project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "examples" / "pages"))

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
            "catalog",
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
                "OPTIONS": {
                    "PAGES_DIR": "catalog/pages",
                },
            },
        ],
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver"],
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
def sample_products():
    """Fixture that returns sample products (import after django.setup())."""
    product_model = importlib.import_module("catalog.models").Product
    return product_model.objects.all()[:3]


@pytest.fixture(autouse=True)
def setup_database() -> None:
    """Setup database (import after django.setup())."""
    product_model = importlib.import_module("catalog.models").Product
    product_model.objects.all().delete()

    product_model.objects.create(title="Product 1", description="Description 1")
    product_model.objects.create(title="Product 2", description="Description 2")
    product_model.objects.create(title="Product 3", description="Description 3")


@pytest.fixture()
def page_modules():
    """Fixture that imports all page modules (after django.setup())."""
    catalog_page = importlib.import_module("catalog.pages.catalog.page")
    main_page = importlib.import_module("catalog.pages.page")
    return {
        "catalog_page": catalog_page,
        "main_page": main_page,
    }
