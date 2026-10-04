"""The shape validators the metadata normaliser compiles the input dicts into.

Each shape checks one value, builds what the fold reads, and names the key path of a
value it refuses.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, ClassVar, Final, Never, Protocol, cast, get_type_hints
from urllib.parse import urlsplit

from django.utils.functional import Promise, lazy

from next.pages.errors import PageMetadataShapeError
from next.utils import WEB_SCHEMES, is_int

from .dicts import LinkDict, Text, Url
from .ld import Node, iter_json, json_problem
from .markers import Alternates, Icon, Link, Replace


X_DEFAULT: Final = "x-default"
"""The hreflang code of the fallback alternate."""

LINK_ATTRS: Final = tuple(
    key for key in get_type_hints(LinkDict) if key not in {"rel", "href"}
)
"""The attributes a `links` entry may carry past `rel` and `href`, in render order."""

ICON_RELS: Final = {"icon": "icon", "apple": "apple-touch-icon"}
"""The rel each named group of `icons` renders under."""

_NO_KEY: Final = object()


@dataclass(slots=True)
class _Walk:
    """One normalise run, the source it reports and the key paths a `Replace` took."""

    source: str
    replaced: set[str] = field(default_factory=set)

    def fail(self, detail: str) -> Never:
        raise PageMetadataShapeError(self.source, detail)


class _Kind(Protocol):
    """One accepted shape of a metadata value, with a cheap test of whether one fits."""

    @property
    def label(self) -> str: ...

    def accepts(self, value: object) -> bool: ...

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object: ...


class _Whole:
    """A shape bound to one key path, never one option of a union.

    It accepts any value and refuses a wrong one while coercing it.
    """

    __slots__ = ()

    def accepts(self, _value: object) -> bool:
        return True


def _is_text(value: object) -> bool:
    return isinstance(value, str | Promise)


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_number(value: object) -> bool:
    return is_int(value) or isinstance(value, float)


def _is_date(value: object) -> bool:
    return isinstance(value, date)


def _is_choice(choices: tuple[object, ...], value: object) -> bool:
    return any(type(value) is type(choice) and value == choice for choice in choices)


def _one(value: object) -> tuple[object, ...]:
    return (value,)


def _keyword(build: Callable[..., object], key: str, value: object) -> object:
    return build(**{key: value})


def _key_phrase(path: str) -> str:
    return f"metadata key {path!r}" if path else "metadata"


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _wrong_type(kind: _Kind, value: object, *, walk: _Walk, path: str) -> Never:
    walk.fail(
        f"declares {_key_phrase(path)} as {type(value).__name__!r}, "
        f"expected {kind.label}"
    )


@dataclass(frozen=True, slots=True)
class _Leaf:
    """A scalar shape decided by one predicate."""

    label: str
    test: Callable[[object], bool]

    def accepts(self, value: object) -> bool:
        return self.test(value)

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if not self.test(value):
            _wrong_type(self, value, walk=walk, path=path)
        return value


def _checked_url(value: object, *, walk: _Walk, path: str) -> str:
    """Return `value` when it is an http or https URL or a path, failing otherwise."""
    if not isinstance(value, str):
        walk.fail(
            f"declares {_key_phrase(path)} as {type(value).__name__!r}, "
            "expected a URL string"
        )
    try:
        scheme = urlsplit(value).scheme
    except ValueError:
        scheme = None
    if scheme != "" and scheme not in WEB_SCHEMES:
        walk.fail(
            f"declares {_key_phrase(path)} as the URL {value!r}, expected an http "
            "or https URL or a path"
        )
    return value


def _forced_url(value: Promise, source: str, path: str) -> str:
    """Force a lazy URL and check it the way a plain one is checked."""
    return _checked_url(str(value), walk=_Walk(source), path=path)


_lazy_url: Final = lazy(_forced_url, str)


def _web_url(value: object, *, walk: _Walk, path: str) -> Url:
    """Check a plain URL now and a lazy one each time it is forced.

    A static dict is read before the URLconf a `reverse_lazy` needs has loaded.
    """
    if isinstance(value, Promise):
        return _lazy_url(value, walk.source, path)
    return _checked_url(value, walk=walk, path=path)


@dataclass(frozen=True, slots=True)
class _Url:
    """A URL a crawler may follow, absolute on http or https or relative."""

    label: ClassVar[str] = "a URL string"

    def accepts(self, value: object) -> bool:
        return _is_text(value)

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        return _web_url(value, walk=walk, path=path)


@dataclass(frozen=True, slots=True)
class _Then:
    """A shape whose checked value is converted once more, a bare form to its block."""

    kind: _Kind
    convert: Callable[[Any], object]

    @property
    def label(self) -> str:
        return self.kind.label

    def accepts(self, value: object) -> bool:
        return self.kind.accepts(value)

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        return self.convert(self.kind.coerce(value, walk=walk, path=path))


@dataclass(frozen=True, slots=True)
class _Items:
    """A sequence whose items all share one shape."""

    item: _Kind

    @property
    def label(self) -> str:
        return f"a sequence where each item is {self.item.label}"

    def accepts(self, value: object) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, str | bytes)

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            _wrong_type(self, value, walk=walk, path=path)
        return tuple(
            self.item.coerce(item, walk=walk, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )


@dataclass(frozen=True, slots=True)
class _Either:
    """The first of several shapes the value fits."""

    options: tuple[_Kind, ...]

    @property
    def label(self) -> str:
        return " or ".join(option.label for option in self.options)

    def accepts(self, value: object) -> bool:
        return any(option.accepts(value) for option in self.options)

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        for option in self.options:
            if option.accepts(value):
                return option.coerce(value, walk=walk, path=path)
        _wrong_type(self, value, walk=walk, path=path)


class _Mapped:
    """The base of the mapping shapes, which accept any mapping until coerced."""

    __slots__ = ()
    label: ClassVar[str] = "a mapping"

    def accepts(self, value: object) -> bool:
        return isinstance(value, Mapping)


@dataclass(frozen=True, slots=True)
class _Block(_Mapped):
    """A mapping of known keys built into one value.

    A `Replace` under a key is unwrapped and its path recorded, if `resettable` allows.
    """

    fields: Mapping[str, _Kind]
    build: Callable[..., object]
    resettable: bool
    required: tuple[str, ...]

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if not isinstance(value, Mapping):
            _wrong_type(self, value, walk=walk, path=path)
        checked: dict[str, Any] = {}
        for key, item in value.items():
            kind = self.fields.get(key)
            if kind is None:
                self._unknown(key, walk=walk, path=path)
            child = _child(path, key)
            given = item
            if self.resettable and isinstance(item, Replace):
                walk.replaced.add(child)
                given = item.value
            if given is not None:
                checked[key] = kind.coerce(given, walk=walk, path=child)
        missing = [key for key in self.required if key not in checked]
        if missing:
            walk.fail(f"declares {_key_phrase(path)} without {missing[0]!r}")
        return self.build(**checked)

    def _unknown(self, key: object, *, walk: _Walk, path: str) -> Never:
        allowed = ", ".join(sorted(self.fields))
        walk.fail(
            f"declares {_key_phrase(_child(path, str(key)))}, expected one of {allowed}"
        )


def _non_str_key(key: object, *, walk: _Walk, path: str) -> Never:
    walk.fail(
        f"declares {_key_phrase(path)} with the key {key!r}, expected string keys"
    )


def _texts(value: object) -> tuple[Text, ...] | None:
    """Return one text or a sequence of texts as a tuple, `None` for anything else."""
    if isinstance(value, str | Promise):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        items = tuple(value)
        if all(map(_is_text, items)):
            return items
    return None


@dataclass(frozen=True, slots=True)
class _Named(_Mapped):
    """A mapping of names to text or texts, fanned out to `(name, text)` pairs.

    A `Replace` under a name records the name, so `RESET` drops an inherited one.
    """

    label: ClassVar[str] = "a mapping of names to text or sequences of text"

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if not isinstance(value, Mapping):
            _wrong_type(self, value, walk=walk, path=path)
        pairs: list[tuple[str, Text]] = []
        for name, item in value.items():
            if not isinstance(name, str):
                _non_str_key(name, walk=walk, path=path)
            child = f"{path}.{name}"
            raw = item
            if isinstance(raw, Replace):
                walk.replaced.add(child)
                raw = raw.value
                if raw is None:
                    continue
            texts = _texts(raw)
            if texts is None:
                walk.fail(
                    f"declares {_key_phrase(child)} as {type(raw).__name__!r}, "
                    "expected text or a sequence of text"
                )
            pairs.extend((name, text) for text in texts)
        return tuple(pairs)


def _json_value(value: object, *, walk: _Walk, path: str) -> object:
    """Return one raw JSON-LD value once every leaf and key in it is one JSON holds.

    A typed node inside is checked when it renders, since a callable builds it late.
    """
    for where, item in iter_json(value, path):
        if isinstance(item, Mapping):
            key = next((key for key in item if not isinstance(key, str)), _NO_KEY)
            if key is not _NO_KEY:
                _non_str_key(key, walk=walk, path=where)
        elif isinstance(item, str | bytes) or not isinstance(item, Sequence):
            problem = json_problem(item)
            if problem is not None:
                shown = item if isinstance(item, float) else type(item).__name__
                walk.fail(
                    f"declares {_key_phrase(where)} as {shown!r}, expected {problem}"
                )
    return value


@dataclass(frozen=True, slots=True)
class _JsonLd(_Whole):
    """One JSON-LD node or a sequence of them, a raw mapping copied into plain ones."""

    label: ClassVar[str] = "a node, a mapping or a sequence of them"

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if isinstance(value, Mapping | Node):
            return (_json_value(value, walk=walk, path=path),)
        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            _wrong_type(self, value, walk=walk, path=path)
        objects: list[object] = []
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            if not isinstance(item, Mapping | Node):
                walk.fail(
                    f"declares {_key_phrase(item_path)} as "
                    f"{type(item).__name__!r}, expected a node or a mapping"
                )
            objects.append(_json_value(item, walk=walk, path=item_path))
        return tuple(objects)


@dataclass(frozen=True, slots=True)
class _Canonical(_Whole):
    """The canonical URL, or `True` for the page itself."""

    label: ClassVar[str] = "a URL string or True"

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if value is True:
            return True
        if value is False:
            walk.fail(
                f"declares {_key_phrase(path)} as False, write RESET to drop an "
                "inherited canonical"
            )
        if not _is_text(value):
            _wrong_type(self, value, walk=walk, path=path)
        return _web_url(value, walk=walk, path=path)


@dataclass(frozen=True, slots=True)
class _Languages(_Whole):
    """The hreflang switch, or a mapping of language codes to URLs."""

    label: ClassVar[str] = "a bool or a mapping of language codes to URLs"

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if isinstance(value, bool):
            return value
        if not isinstance(value, Mapping):
            _wrong_type(self, value, walk=walk, path=path)
        languages: dict[str, Url] = {}
        for code, url in value.items():
            if not isinstance(code, str):
                _non_str_key(code, walk=walk, path=path)
            languages[code] = _web_url(url, walk=walk, path=f"{path}.{code}")
        return languages


@dataclass(frozen=True, slots=True)
class _AlternatesBlock(_Mapped):
    """The alternates block, an `x-default` language moved to `x_default`."""

    block: _Block

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        checked = cast("dict[str, Any]", self.block.coerce(value, walk=walk, path=path))
        languages = checked.get("languages")
        if isinstance(languages, dict):
            if X_DEFAULT in languages:
                if "x_default" in checked:
                    walk.fail(
                        f"declares both {_key_phrase(_child(path, 'x_default'))} and "
                        f"an {X_DEFAULT!r} language, keep one"
                    )
                checked["x_default"] = languages.pop(X_DEFAULT)
            checked["languages"] = tuple(languages.items())
        return Alternates(**checked)


def _icon(**values: str) -> Icon:
    return Icon("", **values)


def _icons(
    icon: tuple[Icon, ...] = (),
    apple: tuple[Icon, ...] = (),
    other: tuple[Icon, ...] = (),
) -> tuple[Icon, ...]:
    """Flatten the groups in render order, each named group taking its rel."""
    named = (
        replace(item, rel=ICON_RELS[group])
        for group, items in (("icon", icon), ("apple", apple))
        for item in items
    )
    return (*named, *other)


def _link(rel: str, href: str, **attrs: Text | bool) -> Link:
    """Build a link, `crossorigin=True` as `anonymous` and `False` left out."""
    if attrs.get("crossorigin") is True:
        attrs["crossorigin"] = "anonymous"
    pairs = tuple(
        (name, value)
        for name in LINK_ATTRS
        if not isinstance(value := attrs.get(name), bool | None)
    )
    return Link(rel, href, pairs)


__all__ = ["ICON_RELS", "LINK_ATTRS", "X_DEFAULT"]
