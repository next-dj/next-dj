import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-!6-apo%7t-ng2%pv-pt2fm!$z*&%x6qfxy7k1^@ebq6on)fcp&"

DEBUG = True

ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "next",
    "django.contrib.staticfiles",
    "home",
    "myapp",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
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

validation = "django.contrib.auth.password_validation"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"{validation}.UserAttributeSimilarityValidator"},
    {"NAME": f"{validation}.MinimumLengthValidator"},
    {"NAME": f"{validation}.CommonPasswordValidator"},
    {"NAME": f"{validation}.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "next.permissions.ObjectPermissionBackend",
]

LOGIN_URL = "admin:login"

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
            "DIRS": [str(BASE_DIR / "chrome")],
            "PAGES_DIR": "pages",
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_components",
        }
    ],
    "STATIC_VERSION": "v1",
    "SITE": {"URL": "https://template.example", "NAME": "next.dj template"},
    "METADATA": {
        "DEFAULTS": {
            "viewport": "width=device-width, initial-scale=1",
            "icons": {
                "icon": {"url": "/static/home/icon.svg", "type": "image/svg+xml"}
            },
            "description": (
                "A next.dj starter with one page root, the shared UI kit and one test."
            ),
            "title": {
                "template": "{title} · {site_name}",
                "default": "Next.dj example template",
            },
        }
    },
}
