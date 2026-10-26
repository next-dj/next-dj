import os
import sys
from pathlib import Path

from config.storages import MANIFEST_STORAGES


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-observability-key-replace-me"

DEBUG = True

ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "next",
    "django.contrib.staticfiles",
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
ASGI_APPLICATION = "config.asgi.application"

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
sys.path.insert(0, str(SHARED_DIR))
STATICFILES_DIRS = [BASE_DIR / "static", SHARED_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

OBS_METRICS_TOKEN = os.environ.get("OBS_METRICS_TOKEN", "")

# The custom `jsx` kind has no insertion verb, so next.W074 fires.
SILENCED_SYSTEM_CHECKS = ["next.W074"]

# Custom components backend, dedup policy and serializer feed the dashboard counters.
NEXT_FRAMEWORK = {
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            # `instrument/` owns the shared HTML envelope every dashboard renders into.
            "DIRS": [str(BASE_DIR / "instrument")],
            "PAGES_DIR": "pages",
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "obs.backends.CountingComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_components",
        }
    ],
    "STATIC_BACKENDS": [
        {
            "BACKEND": "obs.backends.BabelJsxBackend",
            "OPTIONS": {"DEDUP_STRATEGY": "obs.static_policies.InstrumentedDedup"},
        }
    ],
    "JS_CONTEXT_SERIALIZER": "obs.serializers.PydanticJsContextSerializer",
    "SITE": {"URL": "https://observability.example", "NAME": "next.dj observability"},
    "METADATA": {
        "DEFAULTS": {
            "viewport": "width=device-width, initial-scale=1",
            "icons": {"icon": {"url": "/static/obs/icon.svg", "type": "image/svg+xml"}},
            "title": {
                "template": "{title} · {site_name}",
                "default": "next.dj — Observability dashboard",
            },
        }
    },
}
