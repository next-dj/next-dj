"""The strict normaliser that turns one raw metadata mapping into a `Segment`.

The accepted shapes are compiled once at import from the annotations in `dicts.py`.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date, time, timedelta
from decimal import Decimal
from functools import partial
from types import UnionType
from typing import (
    Any,
    ClassVar,
    Final,
    Literal,
    Never,
    Protocol,
    TypeAliasType,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)
from urllib.parse import urlsplit
from uuid import UUID

from django.utils.functional import Promise, lazy

from next.pages.errors import PageMetadataShapeError
from next.utils import WEB_SCHEMES, is_int

from .dicts import (
    AlternatesDict,
    ArticleDict,
    BookDict,
    FeedDict,
    IconDict,
    IconsDict,
    LinkDict,
    MetadataDict,
    OpenGraphAudioDict,
    OpenGraphDict,
    OpenGraphImageDict,
    OpenGraphVideoDict,
    OtherIconDict,
    ProfileDict,
    RobotsDict,
    SiteMetadataDict,
    SiteTitleDict,
    Text,
    ThemeColorDict,
    TitleDict,
    TwitterDict,
    TwitterImageDict,
    TwitterPlayerDict,
    Url,
    VerificationDict,
    ViewportDict,
)
from .ld import Node, Ref
from .markers import (
    Alternates,
    Article,
    Book,
    Feed,
    Icon,
    Link,
    Metadata,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    Profile,
    Replace,
    Robots,
    Segment,
    ThemeColor,
    TitleSpec,
    Twitter,
    TwitterImage,
    TwitterPlayer,
    Verification,
    Viewport,
)


X_DEFAULT: Final = "x-default"
"""The hreflang code of the fallback alternate."""

LINK_ATTRS: Final = tuple(
    key for key in get_type_hints(LinkDict) if key not in {"rel", "href"}
)
"""The attributes a `links` entry may carry past `rel` and `href`, in render order."""

ICON_RELS: Final = {"icon": "icon", "apple": "apple-touch-icon"}
"""The rel each named group of `icons` renders under."""

_JSON_SCALARS: Final = (
    str,
    int,
    float,
    Promise,
    date,
    time,
    timedelta,
    Decimal,
    UUID,
    Ref,
)
"""The leaves a raw JSON-LD value holds, the ones `DjangoJSONEncoder` can write."""


@dataclass(slots=True)
class _Walk:
    """One normalise run, the source it reports and the key paths a `Replace` took."""

    source: str
    replaced: set[str] = field(default_factory=set)

    def fail(self, detail: str) -> Never:
        raise PageMetadataShapeError(self.source, detail)


class _Kind(Protocol):
    """One accepted shape of a metadata value."""

    @property
    def label(self) -> str: ...

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object: ...


class _Option(_Kind, Protocol):
    """A shape that can also say cheaply whether a value is its own."""

    def accepts(self, value: object) -> bool: ...


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

    kind: _Option
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

    options: tuple[_Option, ...]

    @property
    def label(self) -> str:
        return " or ".join(option.label for option in self.options)

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        for option in self.options:
            if option.accepts(value):
                return option.coerce(value, walk=walk, path=path)
        _wrong_type(self, value, walk=walk, path=path)


class _Mapped:
    """The shapes a mapping takes, which accept any mapping and sort it out later."""

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
                _wrong_type(self, value, walk=walk, path=path)
            raw = item
            if isinstance(raw, Replace):
                walk.replaced.add(f"{path}.{name}")
                raw = raw.value
                if raw is None:
                    continue
            texts = _texts(raw)
            if texts is None:
                _wrong_type(self, value, walk=walk, path=path)
            pairs.extend((name, text) for text in texts)
        return tuple(pairs)


def _json_copy(value: object, *, walk: _Walk, path: str) -> object:
    """Copy one JSON-LD value into plain dicts and lists, refusing what JSON lacks."""
    if isinstance(value, Node):
        return value
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                walk.fail(
                    f"declares {_key_phrase(path)} with the key {key!r}, expected "
                    "string keys"
                )
            copied[key] = _json_copy(item, walk=walk, path=f"{path}.{key}")
        return copied
    if isinstance(value, float) and not math.isfinite(value):
        walk.fail(
            f"declares {_key_phrase(path)} as {value!r}, expected a finite number"
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [
            _json_copy(item, walk=walk, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if value is not None and not isinstance(value, _JSON_SCALARS):
        walk.fail(
            f"declares {_key_phrase(path)} as {type(value).__name__!r}, expected a "
            "JSON value"
        )
    return value


@dataclass(frozen=True, slots=True)
class _JsonLd:
    """One JSON-LD node or a sequence of them, a raw mapping copied into plain ones."""

    label: ClassVar[str] = "a node, a mapping or a sequence of them"

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if isinstance(value, Mapping | Node):
            return (_json_copy(value, walk=walk, path=path),)
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
            objects.append(_json_copy(item, walk=walk, path=item_path))
        return tuple(objects)


@dataclass(frozen=True, slots=True)
class _Canonical:
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
class _Languages:
    """The hreflang switch, or a mapping of language codes to URLs."""

    label: ClassVar[str] = "a bool or a mapping of language codes to URLs"

    def coerce(self, value: object, *, walk: _Walk, path: str) -> object:
        if isinstance(value, bool):
            return value
        if not isinstance(value, Mapping) or not all(
            isinstance(code, str) and _is_text(url) for code, url in value.items()
        ):
            _wrong_type(self, value, walk=walk, path=path)
        return {
            code: _web_url(url, walk=walk, path=f"{path}.{code}")
            for code, url in value.items()
        }


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


_LEAVES: Final[Mapping[object, _Option]] = {
    str: _Leaf("a string", _is_str),
    Text: _Leaf("text", _is_text),
    Url: _Url(),
    bool: _Leaf("a bool", _is_bool),
    int: _Leaf("an int", is_int),
    float: _Leaf("a number", _is_number),
    date: _Leaf("a datetime, a date", _is_date),
}
"""The shape of each scalar annotation, the aliases `Text` and `Url` among them."""

_BUILDS: Final[Mapping[object, Callable[..., object]]] = {
    MetadataDict: dict,
    SiteMetadataDict: dict,
    TitleDict: TitleSpec,
    SiteTitleDict: TitleSpec,
    RobotsDict: Robots,
    OpenGraphDict: OpenGraph,
    OpenGraphImageDict: OpenGraphImage,
    OpenGraphVideoDict: OpenGraphVideo,
    OpenGraphAudioDict: OpenGraphAudio,
    ArticleDict: Article,
    ProfileDict: Profile,
    BookDict: Book,
    TwitterDict: Twitter,
    TwitterImageDict: TwitterImage,
    TwitterPlayerDict: TwitterPlayer,
    FeedDict: Feed,
    AlternatesDict: dict,
    VerificationDict: Verification,
    IconDict: _icon,
    OtherIconDict: Icon,
    IconsDict: _icons,
    LinkDict: _link,
    ViewportDict: Viewport,
    ThemeColorDict: ThemeColor,
}
"""The value each input dict builds, the page dicts a plain dict `_segment` splits."""

_SHORTHANDS: Final[Mapping[object, str]] = {
    TitleDict: "text",
    OpenGraphImageDict: "url",
    OpenGraphVideoDict: "url",
    OpenGraphAudioDict: "url",
    TwitterImageDict: "url",
    IconDict: "url",
    ThemeColorDict: "color",
}
"""The key a bare string beside one of these dicts stands for."""

_PATH_KINDS: Final[Mapping[str, _Kind]] = {
    "canonical": _Canonical(),
    "jsonld": _JsonLd(),
    "alternates.languages": _Languages(),
}
"""The keys whose shape their annotation cannot say, by their path."""

_BARE: Final = (str, Text, Url)
"""The scalars a shorthand stands in for."""


def _options(hint: object) -> tuple[object, ...]:
    """Return what a hint admits, unions and aliases flattened and `Replace` dropped."""
    if hint in _LEAVES:
        return (hint,)
    if isinstance(hint, TypeAliasType):
        return _options(hint.__value__)
    origin = get_origin(hint)
    if origin is Union or origin is UnionType:
        return tuple(option for arg in get_args(hint) for option in _options(arg))
    if origin is Replace:
        return ()
    return (hint,)


def _nests(hint: object, block: object) -> bool:
    """Whether `block` appears anywhere inside `hint`."""
    return any(
        option is block or any(_nests(arg, block) for arg in get_args(option))
        for option in _options(hint)
    )


def _kind(hint: object, path: str, outer: frozenset[object]) -> _Kind:
    """Compile the shape of one annotated key.

    A bare value beside a sequence of such values stands for a sequence of one.
    """
    override = _PATH_KINDS.get(path)
    if override is not None:
        return override
    options = _options(hint)
    many = next((option for option in options if get_origin(option) is Sequence), None)
    items = () if many is None else _options(get_args(many)[0])
    short = next(
        (option for option in (*options, *items) if option in _SHORTHANDS), None
    )
    kinds: list[_Option] = []
    for option in options:
        stands = short if short is not None and option in _BARE else option
        kind = _shape(option, path, outer) if stands is option else _shorthand(stands)
        kinds.append(_Then(kind, _one) if stands in items else kind)
    return kinds[0] if len(kinds) == 1 else _Either(tuple(kinds))


def _shorthand(block: object) -> _Option:
    """Return the shape of a bare value that stands for one key of `block`.

    A key the dict does not declare, the text of a title, takes text.
    """
    key = _SHORTHANDS[block]
    kind = _LEAVES[get_type_hints(block).get(key, Text)]
    return _Then(kind, partial(_keyword, _BUILDS[block], key))


def _shape(option: object, path: str, outer: frozenset[object]) -> _Option:
    """Compile one option of a union, a scalar, a sequence, a mapping or a dict."""
    leaf = _LEAVES.get(option)
    if leaf is not None:
        return leaf
    origin = get_origin(option)
    if origin is Literal:
        choices = get_args(option)
        return _Leaf(" or ".join(map(repr, choices)), partial(_is_choice, choices))
    if origin is Sequence:
        return _Items(_kind(get_args(option)[0], f"{path}[]", outer))
    if origin is Mapping:
        return _Named()
    return _block(cast("type[Any]", option), path, outer)


def _block(block: type[Any], path: str, outer: frozenset[object]) -> _Option:
    """Compile an input dict, its keys, the value it builds and what it requires.

    A dict nested in itself drops the key that nests it, so it goes one level deep.
    """
    hints = get_type_hints(block)
    if block in outer:
        hints = {key: hint for key, hint in hints.items() if not _nests(hint, block)}
    inner = outer | {block}
    build = _BUILDS[block]
    order = [item.name for item in fields(build)] if is_dataclass(build) else hints
    compiled = _Block(
        {key: _kind(hint, _child(path, key), inner) for key, hint in hints.items()},
        build,
        resettable=any(
            get_origin(arg) is Replace
            for hint in hints.values()
            for arg in get_args(hint)
        ),
        required=tuple(key for key in order if key in block.__required_keys__),
    )
    return _AlternatesBlock(compiled) if block is AlternatesDict else compiled


_PAGE: Final = _block(MetadataDict, "", frozenset())
_SITE: Final = _block(SiteMetadataDict, "", frozenset())
_OWN: Final = frozenset(get_type_hints(MetadataDict)).intersection(
    item.name for item in fields(Segment)
)


def _segment(block: _Kind, raw: object, source: str) -> Segment:
    """Validate `raw` and split what the segment folds on its own from the rest."""
    walk = _Walk(source)
    checked = cast("dict[str, Any]", block.coerce(raw, walk=walk, path=""))
    own = {key: checked.pop(key) for key in _OWN if key in checked}
    return Segment(
        source, Metadata(**checked), replaced=frozenset(walk.replaced), **own
    )


def normalize_metadata(raw: object, *, source: str) -> Segment:
    """Validate one raw metadata mapping of a `page.py` strictly into a `Segment`.

    A `Promise` stays unevaluated, so a memoised lazy translation resolves per request.
    """
    return _segment(_PAGE, raw, source)


def normalize_site_metadata(raw: object, *, source: str) -> Segment:
    """Validate the settings defaults strictly into the outermost `Segment`."""
    return _segment(_SITE, raw, source)


__all__ = [
    "ICON_RELS",
    "LINK_ATTRS",
    "X_DEFAULT",
    "normalize_metadata",
    "normalize_site_metadata",
]
