"""The schema.org node base and the breadcrumb list a page folds into its `@graph`.

A node is a frozen value, so mypy and `TypeError` catch a missing required property.
`to_json` is the one walk that turns a node or a raw value into its JSON form.
"""

import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, Final
from uuid import UUID
from weakref import WeakKeyDictionary

from django.utils.functional import Promise
from django.utils.timezone import get_current_timezone, is_naive, make_aware


type Text = str | Promise
type UrlMap = Callable[[str], str]

ID: Final = "@id"
TYPE: Final = "@type"

_NO_KEYS: Final[Mapping[str, str]] = MappingProxyType({})
_OWN: Final = frozenset({"id", "type", "extra"})
_SCALARS: Final = (str, int, float, Promise, date, time, timedelta, Decimal, UUID)
"""The leaves `DjangoJSONEncoder` writes, a datetime among the dates."""


def _same(url: str) -> str:
    return url


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def node_id(ident: str) -> str:
    """Return an `@id` as a page resolves it, a bare fragment on the site root.

    `#org` and `/#org` name one node, so the fold and the checks compare this form.
    """
    return f"/{ident}" if ident.startswith("#") else ident


def json_problem(value: object) -> str | None:
    """Return what a JSON leaf should have been, `None` when `value` is one."""
    if isinstance(value, Enum):
        return json_problem(value.value)
    if isinstance(value, float) and not math.isfinite(value):
        return "a finite number"
    if isinstance(value, time) and value.utcoffset() is not None:
        return "a time without a time zone"
    if value is None or isinstance(value, (*_SCALARS, Node, Ref)):
        return None
    return "a JSON value"


def _leaf(value: object) -> object:
    """Return one leaf in the form the encoder writes, refusing what JSON lacks."""
    problem = json_problem(value)
    if problem is not None:
        msg = f"{value!r} is not {problem}"
        raise ValueError(msg)
    if isinstance(value, Enum):
        return _leaf(value.value)
    if isinstance(value, datetime) and is_naive(value):
        return make_aware(value, get_current_timezone())
    return value


def to_json(
    value: object, *, ids: UrlMap = _same, urls: UrlMap | None = None
) -> object:
    """Return a node or a raw value in JSON form, ready for `json.dumps`.

    A nested node renders in place, every `@id` passes through `ids`, and `urls`
    maps the strings of a URL property, the value itself or a sequence's items.
    An enum becomes its value and a naive datetime takes the current time zone.
    A leaf JSON cannot hold, `nan` or a set among them, raises `ValueError`.
    """
    if isinstance(value, Node | Ref):
        return value.as_jsonld(ids)
    if isinstance(value, str):
        return value if urls is None else urls(value)
    if isinstance(value, Mapping):
        return {
            key: ids(item)
            if key == ID and isinstance(item, str)
            else to_json(item, ids=ids)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return [to_json(item, ids=ids, urls=urls) for item in value]
    return _leaf(value)


def iter_json(value: object, path: str = "") -> Iterator[tuple[str, object]]:
    """Yield every value inside a raw JSON value with its path, `value` itself first.

    A node is yielded whole, since it walks its own fields when it renders.
    """
    yield path, value
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from iter_json(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, item in enumerate(value):
            yield from iter_json(item, f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class Ref:
    """A reference to a node declared elsewhere in the graph, by its `@id`."""

    id: str

    def as_jsonld(self, url: UrlMap = _same) -> dict[str, object]:
        """Return the `{"@id": ...}` object of the reference."""
        return {ID: url(self.id)}


type _Plan = tuple[tuple[str, str, bool], ...]

# Weak keys, so a node class an autoreloaded page.py defines leaves with its module.
_PLANS: WeakKeyDictionary[type, _Plan] = WeakKeyDictionary()


@dataclass(frozen=True, slots=True, kw_only=True)
class Node:
    """One schema.org node, its properties spelled snake_case and emitted camelCase.

    `type` overrides the subtype, and `extra` adds verbatim properties last.
    """

    TYPE: ClassVar[str] = "Thing"
    URLS: ClassVar[frozenset[str]] = frozenset()
    KEYS: ClassVar[Mapping[str, str]] = _NO_KEYS

    id: str | None = None
    type: str | None = None
    extra: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def _plan(cls) -> _Plan:
        """Return each property with its key and its URL flag, read once."""
        plan = _PLANS.get(cls)
        if plan is None:
            plan = _PLANS[cls] = tuple(
                (
                    item.name,
                    cls.KEYS.get(item.name, _camel(item.name)),
                    item.name in cls.URLS,
                )
                for item in fields(cls)
                if item.name not in _OWN
            )
        return plan

    def as_jsonld(self, url: UrlMap = _same) -> dict[str, object]:
        """Return the node as a JSON-LD object, `None` and empty sequences dropped.

        `url` maps the `@id` and every URL property, so resolve can absolutize them.
        A property JSON cannot hold raises `ValueError`.
        """
        out: dict[str, object] = {TYPE: self.type or self.TYPE}
        if self.id is not None:
            out[ID] = url(self.id)
        for name, key, is_url in self._plan():
            value = getattr(self, name)
            if value is None or (isinstance(value, tuple) and not value):
                continue
            out[key] = to_json(value, ids=url, urls=url if is_url else None)
        out.update((key, to_json(item, ids=url)) for key, item in self.extra.items())
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class ListItem(Node):
    """One position of a `BreadcrumbList`, the last one without an `item` allowed."""

    TYPE: ClassVar[str] = "ListItem"
    URLS: ClassVar[frozenset[str]] = frozenset({"item"})

    name: Text
    position: int
    item: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BreadcrumbList(Node):
    """The trail from the site root down to one page."""

    TYPE: ClassVar[str] = "BreadcrumbList"
    KEYS: ClassVar[Mapping[str, str]] = MappingProxyType({"items": "itemListElement"})

    items: tuple[ListItem, ...]


def raw_id(item: object) -> str | None:
    """Return the `@id` a node or a raw mapping declares, a bare fragment rooted."""
    ident = item.id if isinstance(item, Node) else None
    if isinstance(item, Mapping):
        found = item.get(ID)
        ident = found if isinstance(found, str) else None
    return None if ident is None else node_id(ident)


def node_type(item: object) -> str | None:
    """Return the `@type` a node or a raw mapping declares."""
    if isinstance(item, Node):
        return item.type or item.TYPE
    kind = item.get(TYPE) if isinstance(item, Mapping) else None
    return kind if isinstance(kind, str) else None


__all__ = ["BreadcrumbList", "ListItem", "Node", "Ref"]
