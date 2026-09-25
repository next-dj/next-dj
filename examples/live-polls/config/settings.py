import os
import sys
from pathlib import Path

from next.conf import extend_default_backend


BASE_DIR = Path(__file__).resolve().parent.parent

# `VITE_DEV_ORIGIN` wins when set, an empty value included, so an empty one asks for the
# built manifest. Under pytest a stub origin keeps asset URLs identical everywhere
# without a build, and with no manifest on disk the local Vite dev server is the
# default, so `runserver` plus `npm run dev` needs no env-var ceremony.
_VITE_MANIFEST_PATH = BASE_DIR / "polls/static/polls/dist/.vite/manifest.json"
VITE_DEV_ORIGIN = os.environ.get("VITE_DEV_ORIGIN", "")
if "VITE_DEV_ORIGIN" not in os.environ:
    if "pytest" in sys.modules:
        VITE_DEV_ORIGIN = "http://test-vite.invalid"
    elif not _VITE_MANIFEST_PATH.exists():
        VITE_DEV_ORIGIN = "http://localhost:5173"

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
        "LOCATION": "live-polls",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

SHARED_DIR = BASE_DIR.parent / "_shared"
sys.path.insert(0, str(SHARED_DIR))
STATICFILES_DIRS = [SHARED_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

NEXT_FRAMEWORK = {
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            # `studio/` is the project-level page root for the live-polls
            # broadcast. It supplies the shared HTML envelope wrapped
            # around every poll list, detail, and stream surface.
            "DIRS": [str(BASE_DIR / "studio")],
            "PAGES_DIR": "screens",
            "OPTIONS": {"context_processors": []},
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_widgets",
        }
    ],
    "STATIC_BACKENDS": [
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
        }
    ],
    "PARTIAL_BACKENDS": extend_default_backend(
        "PARTIAL_BACKENDS", OPTIONS={"VERSION": "v1"}
    ),
    "METADATA": {
        "DEFAULTS": {
            "site_name": "next.dj polls",
            "title": {
                "template": "{title} · {site_name}",
                "default": "next.dj — Live polls",
            },
        }
    },
}
