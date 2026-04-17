import sys
from pathlib import Path

import django
from django.conf import settings


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
        BASE_DIR=project_root,
        STATIC_URL="/static/",
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        },
        USE_TZ=True,
        TIME_ZONE="UTC",
        NEXT_FRAMEWORK={
            "DEFAULT_PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                    "APP_DIRS": False,
                    "DIRS": [str(project_root / "tests" / "pages")],
                    "OPTIONS": {},
                },
            ],
        },
    )
    # Register form actions from test_forms before URLconf is loaded (django.setup()
    # Loads next.urls and builds urlpatterns. Actions must be in form_action_manager.
    # by then so that the form_action URL pattern is included).
    import tests.test_forms  # noqa: F401

    django.setup()

# Shared fixtures
pytest_plugins = ["tests.fixtures"]
