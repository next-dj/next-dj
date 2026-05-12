import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

# Vue asset resolution rule, in priority order.
#  1. `VITE_DEV_ORIGIN` env var (any value): explicit override wins.
#  2. A built manifest exists on disk: production-shaped run after
#     `npm run build`. The backend reads hashed bundle URLs.
#  3. Neither: assume the developer is running `npm run dev` and
#     default to the local Vite dev server. `runserver` plus
#     `npm run dev` then works without env-var ceremony.
#  4. Pytest mode: a stub origin so the static collector stops short
#     of looking for a manifest the test never built.
_VITE_MANIFEST_PATH = BASE_DIR / "polls/static/polls/dist/.vite/manifest.json"
VITE_DEV_ORIGIN = os.environ.get("VITE_DEV_ORIGIN", "")
if not VITE_DEV_ORIGIN and not _VITE_MANIFEST_PATH.exists():
    VITE_DEV_ORIGIN = "http://localhost:5173"
if "pytest" in sys.modules:
    VITE_DEV_ORIGIN = "http://test-vite.invalid"

SECRET_KEY = "django-insecure-live-polls-replace-me"

DEBUG = True

ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "next",
    "polls",
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
        "LOCATION": "live-polls",
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

SHARED_DIR = BASE_DIR.parent / "_shared"
STATICFILES_DIRS = [SHARED_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            # `studio/` is the project-level page root for the live-polls
            # broadcast. It supplies the shared HTML envelope wrapped
            # around every poll list, detail, and stream surface.
            "DIRS": [str(BASE_DIR / "studio")],
            "PAGES_DIR": "screens",
            "OPTIONS": {
                "context_processors": [],
            },
        },
    ],
    "DEFAULT_COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_widgets",
        },
    ],
    "DEFAULT_STATIC_BACKENDS": [
        {
            "BACKEND": "polls.backends.ViteManifestBackend",
            "OPTIONS": {
                "DEDUP_STRATEGY": "next.static.collector.HashContentDedup",
                "JS_CONTEXT_POLICY": "next.static.collector.DeepMergePolicy",
                "DEV_ORIGIN": VITE_DEV_ORIGIN,
                "VITE_ROOT": str(BASE_DIR),
                "MANIFEST_PATH": str(
                    BASE_DIR / "polls/static/polls/dist/.vite/manifest.json"
                ),
            },
        },
    ],
}
