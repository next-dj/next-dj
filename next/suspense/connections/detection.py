"""Deployment facts that make pool jobs unsafe, detected once per process.

Each source reads `kind:detail`, and a backend covers it by the kind or the full name.
"""

import functools
from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import Final

from django.conf import settings
from django.utils.module_loading import import_string

from next.conf.defer import thread_local_middleware

from .backends import seams


ROUTERS: Final = "routers"
TENANT: Final = "tenant-middleware"
THREAD_LOCAL: Final = "thread-local-middleware"

TENANT_MIDDLEWARE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "django-tenants": "django_tenants.middleware",
        "django-tenant-schemas": "tenant_schemas.middleware",
    }
)
"""The package of each known tenant middleware, which pins a schema per connection."""

THREAD_LOCAL_LIBRARIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "django-crum": "crum",
        "django-multitenant": "django_multitenant",
        "django-scopes": "django_scopes",
        "django-tenant-users": "tenant_users",
        "django-threadlocals": "threadlocals",
        "django-currentuser": "django_currentuser",
    }
)
"""The import root of each library that scopes queries through `threading.local`."""

_STATELESS: Final = "next_stateless"


def _under(name: str, root: str) -> bool:
    return name == root or name.startswith(f"{root}.")


def _router_sources() -> Iterator[str]:
    """Yield the routers that do not declare `next_stateless = True`."""
    for entry in settings.DATABASE_ROUTERS:
        if isinstance(entry, str):
            name = entry
            try:
                router = import_string(entry)
            except ImportError:
                router = None
        else:
            router = entry
            name = f"{type(entry).__module__}.{type(entry).__qualname__}"
        if getattr(router, _STATELESS, False) is not True:
            yield f"{ROUTERS}:{name}"


def _tenant_sources() -> Iterator[str]:
    for path in settings.MIDDLEWARE:
        if any(_under(path, root) for root in TENANT_MIDDLEWARE.values()):
            yield f"{TENANT}:{path}"


def _library_sources() -> Iterator[str]:
    for entry in (*settings.INSTALLED_APPS, *settings.MIDDLEWARE):
        for library, root in THREAD_LOCAL_LIBRARIES.items():
            if _under(entry, root):
                yield f"{library}:{entry}"


def _uncovered(found: Iterable[str]) -> tuple[str, ...]:
    """Return the sources no configured backend covers, in order and without repeats."""
    covers = seams().covers
    return tuple(
        source
        for source in dict.fromkeys(found)
        if source not in covers and source.partition(":")[0] not in covers
    )


@functools.cache
def connection_sources() -> tuple[str, ...]:
    """Return the per-connection state sources no `ConnectionState` covers.

    A `connection_created` receiver is none, as it runs for a pool connection as well.
    """
    return _uncovered((*_router_sources(), *_tenant_sources()))


@functools.cache
def thread_sources() -> tuple[str, ...]:
    """Return the thread-local scoping sources no `ThreadState` covers."""
    listed = (f"{THREAD_LOCAL}:{path}" for path in thread_local_middleware())
    return _uncovered((*_library_sources(), *listed))


def forget_detection(**_: object) -> None:
    """Drop the backends and detected sources, so the next read sees new settings."""
    seams.cache_clear()
    connection_sources.cache_clear()
    thread_sources.cache_clear()


__all__ = [
    "ROUTERS",
    "TENANT",
    "TENANT_MIDDLEWARE",
    "THREAD_LOCAL",
    "THREAD_LOCAL_LIBRARIES",
    "connection_sources",
    "forget_detection",
    "thread_sources",
]
