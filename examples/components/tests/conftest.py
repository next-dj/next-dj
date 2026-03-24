import importlib.util
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.test import Client


example_root = Path(__file__).resolve().parent.parent
repo_root = example_root.parent.parent
sys.path.insert(0, str(example_root))
sys.path.insert(0, str(repo_root))

if not settings.configured:
    settings.configure(
        SECRET_KEY="django-insecure-example-key-for-components",
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            },
        },
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "next",
            "myapp",
        ],
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "myapp.middleware.LoginRequiredForPostEditorMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        ROOT_URLCONF="config.urls",
        TEMPLATES=[
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
        ],
        NEXT_PAGES=[
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": True,
                "OPTIONS": {"COMPONENTS_DIR": "_components"},
            },
        ],
        NEXT_COMPONENTS=[
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "APP_DIRS": True,
                "OPTIONS": {
                    "COMPONENTS_DIR": "_components",
                    "COMPONENTS_DIRS": [str(example_root / "root_components")],
                },
            },
        ],
        USE_TZ=True,
        TIME_ZONE="UTC",
        ALLOWED_HOSTS=["testserver"],
        LOGIN_URL="/account/login/",
        LOGOUT_REDIRECT_URL="/",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

    import myapp.pages.account.login.page
    import myapp.pages.account.profile.page
    import myapp.pages.account.register.page
    import myapp.pages.page
    import myapp.pages.posts.create.page  # noqa: F401

    authors_page = example_root / "myapp" / "pages" / "authors" / "[int:id]" / "page.py"
    spec = importlib.util.spec_from_file_location("myapp_authors_page", authors_page)
    authors_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(authors_mod)

    for sub in ("edit", "details"):
        path = example_root / "myapp" / "pages" / "posts" / "[int:id]" / sub / "page.py"
        name = f"myapp_posts_bracket_{sub}"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)


@pytest.fixture()
def client() -> Client:
    """Django test client fixture."""
    return Client()
