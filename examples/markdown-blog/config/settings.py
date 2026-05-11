from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-markdown-blog-replace-me"

DEBUG = True

ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "next",
    "blog",
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
            # `site/` holds the project-level HTML envelope plus the
            # `site_footer` project-shared component. The file router
            # walks this root alongside `blog/screens/`, and components
            # under `site/_parts/` become root-scope visible everywhere.
            "DIRS": [str(BASE_DIR / "site")],
            "PAGES_DIR": "screens",
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "blog.context_processors.site_nav",
                ],
            },
        },
    ],
    "DEFAULT_COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            # Two extra component roots: the cross-project shadcn kit AND
            # the project-level `_parts/` next to the shared layout. The
            # second entry makes `site_footer` resolve at the empty route
            # scope across every template in the project.
            "DIRS": [
                str(SHARED_DIR / "_components"),
                str(BASE_DIR / "site" / "_parts"),
            ],
            "COMPONENTS_DIR": "_parts",
        },
    ],
    "TEMPLATE_LOADERS": [
        "blog.loaders.MarkdownTemplateLoader",
        "next.pages.loaders.DjxTemplateLoader",
    ],
}
