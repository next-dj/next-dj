import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


BROKER_POOL = 4096
CACHE_ENTRIES = 100_000


def broker_url(url: str) -> str:
    """Return the Redis URL with a pool sized for every harness subscription.

    redis-py pools 100 connections by default and each subscription holds one.
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.setdefault("max_connections", str(BROKER_POOL))
    return urlunsplit(parts._replace(query=urlencode(query)))


SECRET_KEY = "next-dj-load-harness-not-a-secret"

DEBUG = False

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "next",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "tests.load.urls"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "next-dj-load",
        "OPTIONS": {"MAX_ENTRIES": CACHE_ENTRIES},
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"

USE_TZ = True

TIME_ZONE = "UTC"

NEXT_FRAMEWORK = {
    "REALTIME": {
        "BROKER": {
            "BACKEND": "next.realtime.RedisBroker",
            "OPTIONS": {
                "URL": broker_url(os.environ["REDIS_URL"]),
                "PREFIX": os.environ["LOAD_REDIS_PREFIX"],
            },
        },
        "TRANSPORTS": ["ws", "sse"],
    }
}
