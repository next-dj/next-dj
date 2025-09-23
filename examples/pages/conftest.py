import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client, RequestFactory

# add project root to python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
# add examples/pages to python path for catalog app
sys.path.insert(0, str(project_root / "examples" / "pages"))

# configure django settings for pages example
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
                "OPTIONS": {
                    "init_command": "PRAGMA foreign_keys=OFF;",
                },
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
def sample_products():
    """fixture that returns sample products."""
    from catalog.models import Product

    return Product.objects.all()[:3]


@pytest.fixture(autouse=True)
def setup_database():
    """setup database for all tests."""
    from catalog.models import Product

    # no migrations needed for tests

    # clear existing products
    Product.objects.all().delete()

    # create sample products
    Product.objects.create(title="Product 1", description="Description 1")
    Product.objects.create(title="Product 2", description="Description 2")
    Product.objects.create(title="Product 3", description="Description 3")


@pytest.fixture
def page_modules():
    """fixture that imports all page modules."""
    import catalog.pages.catalog.page as catalog_page
    import catalog.pages.page as main_page

    return {
        "catalog_page": catalog_page,
        "main_page": main_page,
    }
