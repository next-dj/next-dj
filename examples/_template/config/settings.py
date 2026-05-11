from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-template-key-replace-me"

DEBUG = True

ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "next",
    # Add your app here
    "myapp",
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
        "LOCATION": "next-example-template",
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# The shared next.dj examples UI kit lives one level up under
# examples/_shared/. Pull its static files and root component tree
# into this project so templates can render shadcn-style components
# (button, card, badge, …) via `{% component "button" %}`.
SHARED_DIR = BASE_DIR.parent / "_shared"
STATICFILES_DIRS = [BASE_DIR / "static", SHARED_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Every example overrides PAGES_DIR and COMPONENTS_DIR to showcase the
# convention. Pick names that fit your domain. For single-key overrides,
# `next.conf.extend_default_backend` is a shorter alternative — see
# docs/content/guide/project-layout.rst (section "Settings helpers").
NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            # Project-level page root: contains the HTML envelope layout
            # (and may host project-shared components under `_widgets/`).
            # Demonstrates `DEFAULT_PAGE_BACKENDS["DIRS"]` working alongside
            # `APP_DIRS=True`.
            "DIRS": [str(BASE_DIR / "chrome")],
            "PAGES_DIR": "routes",
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
}
