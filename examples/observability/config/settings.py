from pathlib import Path


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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    },
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "next-example-observability",
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Naming, custom backend, dedup policy, and JS-context serializer wiring
# all live under one settings dict. The custom components backend counts
# every name resolution. The dedup policy counts every asset filtered as
# a duplicate. The pluggable serializer encodes every value reaching
# `window.Next.context` through a pydantic-aware encoder by default. One
# decorator on the live stats page swaps this serializer for the same
# class explicitly so the override path is exercised end to end.
NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            "DIRS": [],
            "PAGES_DIR": "dashboards",
            "OPTIONS": {
                "context_processors": [],
            },
        },
    ],
    "DEFAULT_COMPONENT_BACKENDS": [
        {
            "BACKEND": "obs.backends.CountingComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "_widgets",
        },
    ],
    "DEFAULT_STATIC_BACKENDS": [
        {
            "BACKEND": "obs.backends.BabelJsxBackend",
            "OPTIONS": {
                "DEDUP_STRATEGY": "obs.static_policies.InstrumentedDedup",
            },
        },
    ],
    "JS_CONTEXT_SERIALIZER": "obs.serializers.PydanticJsContextSerializer",
}
