import os
from pathlib import Path

from next.conf import extend_default_backend


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-kanban-replace-me"

DEBUG = True

ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "next",
    "kanban",
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
        "LOCATION": "kanban",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

SHARED_DIR = BASE_DIR.parent / "_shared"
STATICFILES_DIRS = [SHARED_DIR / "static"]

VITE_DEV_ORIGIN = "http://localhost:5173"
VITE_MANIFEST = BASE_DIR / "kanban/static/kanban/dist/.vite/manifest.json"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

NEXT_FRAMEWORK = {
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            # Project-level page root carrying the shared HTML envelope.
            "DIRS": [str(BASE_DIR / "cockpit")],
            "PAGES_DIR": "boards",
            "OPTIONS": {"context_processors": []},
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_pieces",
        }
    ],
    "STATIC_BACKENDS": [
        {
            "BACKEND": "kanban.backends.ViteManifestBackend",
            "OPTIONS": {
                "DEDUP_STRATEGY": "next.static.collector.HashContentDedup",
                "JS_CONTEXT_POLICY": "next.static.collector.DeepMergePolicy",
                # The HMR receiver reads this value back from the backend
                # options, so one variable moves every dev-server URL. An empty
                # value is what routes assets through the built manifest.
                "DEV_ORIGIN": os.environ.get("VITE_ORIGIN", VITE_DEV_ORIGIN),
                "VITE_ROOT": str(BASE_DIR),
                "MANIFEST_PATH": str(VITE_MANIFEST),
            },
        }
    ],
    # Assets are served from disk, so no hashed manifest exists to derive an
    # asset version from and the default sentinel would leave the guard silent.
    "PARTIAL_BACKENDS": extend_default_backend(
        "PARTIAL_BACKENDS", OPTIONS={"VERSION": "v1"}
    ),
}
