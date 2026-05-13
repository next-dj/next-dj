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
    "django.contrib.staticfiles",
    "next",
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
    "shadcn_admin.middleware.AdminPermissionMiddleware",
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
        "LOCATION": "admin-example",
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

LOGIN_URL = "/admin/login/"

NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            # `chrome/` is the project-level page root. It only holds
            # `layout.djx` — the outermost HTML envelope (DOCTYPE,
            # `<body>`, `{% collect_scripts %}`) that wraps every page.
            # Per-app routes live under each app's `surfaces/` tree,
            # picked up by `APP_DIRS=True` + `PAGES_DIR="surfaces"`.
            "APP_DIRS": True,
            "DIRS": [str(BASE_DIR / "chrome")],
            "PAGES_DIR": "surfaces",
            "OPTIONS": {"context_processors": []},
        },
    ],
    "DEFAULT_COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [
                str(BASE_DIR / "shadcn_admin" / "_panels"),
                str(SHARED_DIR / "_components"),
            ],
            "COMPONENTS_DIR": "_panels",
        },
    ],
}
