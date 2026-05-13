from __future__ import annotations

import sys
from pathlib import Path

import django
from django.conf import settings


PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def _install_project_root_on_path() -> None:
    root_str = str(PROJECT_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _build_test_settings() -> dict[str, object]:
    return {
        "DEBUG": True,
        "DATABASES": {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            },
        },
        "TEMPLATES": [
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
        "INSTALLED_APPS": [
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "next",
        ],
        "MIDDLEWARE": [
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        "ROOT_URLCONF": "next.urls",
        "SECRET_KEY": "test-secret-key",
        "BASE_DIR": PROJECT_ROOT,
        "STATIC_URL": "/static/",
        "STORAGES": {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        },
        "USE_TZ": True,
        "TIME_ZONE": "UTC",
        "NEXT_FRAMEWORK": {
            "DEFAULT_PAGE_BACKENDS": [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                    "APP_DIRS": False,
                    "DIRS": [str(PROJECT_ROOT / "tests" / "site_pages")],
                    "OPTIONS": {},
                },
            ],
        },
    }


def setup() -> None:
    """Configure Django settings and run `django.setup()` if not already done."""
    _install_project_root_on_path()
    if settings.configured:
        return

    settings.configure(**_build_test_settings())
    # Register form actions before URL conf loads: `django.setup()` loads
    # `next.urls` which builds url patterns, so `@action` handlers must be
    # in the form-action registry by then.

    django.setup()


__all__ = ["setup"]
