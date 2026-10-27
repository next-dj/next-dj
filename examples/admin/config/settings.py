import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-admin-example-replace-me"

DEBUG = True

ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "next",
    "django.contrib.staticfiles",
    "shadcn_admin",
    "library",
    "admin_audit",
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
        "LOCATION": "admin-example",
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

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "next.permissions.ObjectPermissionBackend",
]

LOGIN_URL = "/admin/login/"

NEXT_FRAMEWORK = {
    "LOGIN_REQUIRED": True,
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            "DIRS": [str(BASE_DIR / "chrome")],
            "PAGES_DIR": "pages",
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.csrf",
                    "shadcn_admin.context_processors.admin_shell",
                ]
            },
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [
                str(BASE_DIR / "shadcn_admin" / "_components"),
                str(SHARED_DIR / "_components"),
            ],
            "COMPONENTS_DIR": "_components",
        }
    ],
    "STATIC_VERSION": "v1",
    "SITE": {
        "URL": "https://admin.example",
        "NAME": "next.dj admin",
        "INDEXABLE": False,
    },
    "METADATA": {
        "DEFAULTS": {
            "viewport": "width=device-width, initial-scale=1",
            "icons": {
                "icon": {
                    "url": "/static/shadcn_admin/icon.svg",
                    "type": "image/svg+xml",
                }
            },
            "title": {"template": "{title} · {site_name}", "default": "next.dj admin"},
        }
    },
}
