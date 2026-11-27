"""Give each job its own lazy querysets, so no two jobs evaluate a shared one."""

import copy
from collections.abc import Mapping
from typing import Any, Final, TypeGuard

from django.core.paginator import Page
from django.db.models import QuerySet


_CACHE: Final = "_result_cache"
_LAZY: Final = (QuerySet, Page)


def _unevaluated(value: object) -> TypeGuard[QuerySet[Any, Any]]:
    """Whether `value` is a queryset that has not fetched its rows yet.

    Django exposes no public flag, so this reads the cache its deletion collector reads.
    """
    return isinstance(value, QuerySet) and vars(value).get(_CACHE) is None


def _fresh(value: object) -> object:
    if _unevaluated(value):
        return value.all()
    if isinstance(value, Page) and _unevaluated(value.object_list):
        page = copy.copy(value)
        page.object_list = value.object_list.all()
        return page
    return value


def fresh_querysets(data: Mapping[str, object]) -> dict[str, object]:
    """Return a shallow copy with every unevaluated top-level queryset cloned, lazily.

    A Django `Page` gets a copy whose `object_list` is cloned the same way.
    """
    return {
        key: _fresh(value) if isinstance(value, _LAZY) else value
        for key, value in data.items()
    }


__all__ = ["fresh_querysets"]
