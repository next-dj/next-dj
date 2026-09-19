import os
from pathlib import Path

from config.storages import MANIFEST_STORAGES
from next.conf import extend_default_backend


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-observability-key-replace-me"

DEBUG = True

ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "next",
    "obs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "next-example-observability",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

if os.environ.get("OBS_STATIC_MANIFEST") == "1":
    STORAGES = MANIFEST_STORAGES

SHARED_DIR = BASE_DIR.parent / "_shared"
STATICFILES_DIRS = [BASE_DIR / "static", SHARED_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# `obs/apps.py` registers the `jsx` kind with a custom Babel renderer, so the client has
# no insertion verb and next.W074 fires. The sparkline sits outside every zone anyway.
SILENCED_SYSTEM_CHECKS = ["next.W074"]

# The custom components backend counts every name resolution, the dedup policy counts
# every filtered duplicate, and the serializer encodes `window.Next.context`.
NEXT_FRAMEWORK = {
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            # `instrument/` owns the shared HTML envelope every dashboard renders into.
            "DIRS": [str(BASE_DIR / "instrument")],
            "PAGES_DIR": "dashboards",
            "OPTIONS": {"context_processors": []},
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "obs.backends.CountingComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_widgets",
        }
    ],
    "STATIC_BACKENDS": [
        {
            "BACKEND": "obs.backends.BabelJsxBackend",
            "OPTIONS": {"DEDUP_STRATEGY": "obs.static_policies.InstrumentedDedup"},
        }
    ],
    "JS_CONTEXT_SERIALIZER": "obs.serializers.PydanticJsContextSerializer",
    # Without the manifest profile assets are served from disk, so no hashed manifest
    # exists to derive an asset version from and the sentinel leaves the guard silent.
    "PARTIAL_BACKENDS": extend_default_backend(
        "PARTIAL_BACKENDS", OPTIONS={"VERSION": "v1"}
    ),
}
