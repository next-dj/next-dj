"""The schema.org node base and the breadcrumb list a page folds into its `@graph`.

A node is a frozen value, so mypy and `TypeError` catch a missing required property.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, Final

from django.utils.functional import Promise


type Text = str | Promise
type UrlMap = Callable[[str], str]

ID: Final = "@id"
TYPE: Final = "@type"

_NO_KEYS: Final[Mapping[str, str]] = MappingProxyType({})
_OWN: Final = frozenset({"id", "type", "extra"})


def _same(url: str) -> str:
    return url


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


@dataclass(frozen=True, slots=True)
class Ref:
    """A reference to a node declared elsewhere in the graph, by its `@id`."""

    id: str

    def as_jsonld(self, url: UrlMap = _same) -> dict[str, object]:
        """Return the `{"@id": ...}` object of the reference."""
        return {ID: url(self.id)}


type _Plan = tuple[tuple[str, str, bool, str | None], ...]

_PLANS: dict[type, _Plan] = {}


def plain(value: object, url: UrlMap = _same) -> object:
    """Return one value in JSON form, nested nodes and references rendered in place."""
    if isinstance(value, Node | Ref):
        return value.as_jsonld(url)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple | list):
        return [plain(item, url) for item in value]
    if isinstance(value, Mapping):
        return {key: plain(item, url) for key, item in value.items()}
    return value


def _url_value(value: object, url: UrlMap) -> object:
    if isinstance(value, str):
        return url(value)
    if isinstance(value, tuple):
        return [
            url(item) if isinstance(item, str) else plain(item, url) for item in value
        ]
    return plain(value, url)


@dataclass(frozen=True, slots=True, kw_only=True)
class Node:
    """One schema.org node, its properties spelled snake_case and emitted camelCase.

    `type` overrides the subtype, and `extra` adds verbatim properties last.
    """

    TYPE: ClassVar[str] = "Thing"
    URLS: ClassVar[frozenset[str]] = frozenset()
    KEYS: ClassVar[Mapping[str, str]] = _NO_KEYS
    TEXTS: ClassVar[Mapping[str, str]] = _NO_KEYS

    id: str | None = None
    type: str | None = None
    extra: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def _plan(cls) -> _Plan:
        """Return each property with its key, URL flag and text wrapper, read once."""
        plan = _PLANS.get(cls)
        if plan is None:
            plan = _PLANS[cls] = tuple(
                (
                    item.name,
                    cls.KEYS.get(item.name, _camel(item.name)),
                    item.name in cls.URLS,
                    cls.TEXTS.get(item.name),
                )
                for item in fields(cls)
                if item.name not in _OWN
            )
        return plan

    def as_jsonld(self, url: UrlMap = _same) -> dict[str, object]:
        """Return the node as a JSON-LD object, `None` and empty sequences dropped.

        `url` maps the `@id` and every URL property, so resolve can absolutize them.
        """
        out: dict[str, object] = {TYPE: self.type or self.TYPE}
        if self.id is not None:
            out[ID] = url(self.id)
        for name, key, is_url, text in self._plan():
            value = getattr(self, name)
            if value is None or (isinstance(value, tuple) and not value):
                continue
            if text is not None:
                out[key] = {TYPE: text, "text": value}
            elif is_url:
                out[key] = _url_value(value, url)
            else:
                out[key] = plain(value, url)
        out.update((key, plain(item, url)) for key, item in self.extra.items())
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
    """Return the `@id` a node or a raw mapping declares, before any resolve."""
    if isinstance(item, Node):
        return item.id
    ident = item.get(ID) if isinstance(item, Mapping) else None
    return ident if isinstance(ident, str) else None


def node_type(item: object) -> str | None:
    """Return the `@type` a node or a raw mapping declares."""
    if isinstance(item, Node):
        return item.type or item.TYPE
    kind = item.get(TYPE) if isinstance(item, Mapping) else None
    return kind if isinstance(kind, str) else None


__all__ = ["BreadcrumbList", "ListItem", "Node", "Ref"]
