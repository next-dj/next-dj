"""The metadata input contract, its immutable folded form and the strict normaliser."""

import functools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Never, Protocol, TypedDict

from django.utils.functional import Promise

from next.pages.errors import PageMetadataShapeError


type Text = str | Promise


class TitleDict(TypedDict, total=False):
    """The dict form of a page title."""

    template: Text
    default: Text
    absolute: Text


class SiteTitleDict(TypedDict, total=False):
    """The dict form of the site title, which only seeds the chain."""

    template: Text
    default: Text


class RobotsDict(TypedDict, total=False):
    """The robots directives as flags and limits."""

    index: bool
    follow: bool
    noarchive: bool
    nosnippet: bool
    noimageindex: bool
    notranslate: bool
    unavailable_after: str
    max_snippet: int
    max_image_preview: str
    max_video_preview: int
    googlebot: "RobotsDict | str"


class OpenGraphImageDict(TypedDict, total=False):
    """One Open Graph image with its optional dimensions and alt text."""

    url: str
    width: int
    height: int
    alt: Text


class ArticleDict(TypedDict, total=False):
    """The `article:*` Open Graph properties."""

    published_time: datetime | str
    modified_time: datetime | str
    authors: Sequence[str]
    section: Text
    tags: Sequence[Text]


class OpenGraphDict(TypedDict, total=False):
    """The Open Graph block."""

    title: Text
    description: Text
    url: str
    type: str
    site_name: Text
    locale: str
    images: Sequence[OpenGraphImageDict | str]
    article: ArticleDict


class TwitterDict(TypedDict, total=False):
    """The Twitter card block."""

    card: str
    site: str
    creator: str
    title: Text
    description: Text
    images: Sequence[str]


class AlternatesDict(TypedDict, total=False):
    """The hreflang alternates."""

    languages: Mapping[str, str] | bool
    x_default: str


class VerificationDict(TypedDict, total=False):
    """Site verification tokens per search engine."""

    google: str | Sequence[str]
    yandex: str | Sequence[str]
    bing: str | Sequence[str]


class _SharedMetadataDict(TypedDict, total=False):
    description: Text
    base: str
    site_name: Text
    canonical: str | bool
    alternates: AlternatesDict
    robots: RobotsDict | str
    og: OpenGraphDict
    twitter: TwitterDict
    verification: VerificationDict
    other: Mapping[str, Text | Sequence[Text]]
    jsonld: Mapping[str, object] | Sequence[Mapping[str, object]]


class MetadataDict(_SharedMetadataDict, total=False):
    """The metadata a `page.py` declares as a dict or returns from its callable."""

    title: Text | TitleDict


class SiteMetadataDict(_SharedMetadataDict, total=False):
    """The metadata `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` seeds the chain with."""

    title: SiteTitleDict


@dataclass(frozen=True, slots=True)
class Robots:
    """The folded robots directives."""

    index: bool | None = None
    follow: bool | None = None
    noarchive: bool | None = None
    nosnippet: bool | None = None
    noimageindex: bool | None = None
    notranslate: bool | None = None
    unavailable_after: str | None = None
    max_snippet: int | None = None
    max_image_preview: str | None = None
    max_video_preview: int | None = None
    googlebot: "Robots | str | None" = None


@dataclass(frozen=True, slots=True)
class OpenGraphImage:
    """One folded Open Graph image."""

    url: str | None = None
    width: int | None = None
    height: int | None = None
    alt: Text | None = None


@dataclass(frozen=True, slots=True)
class Article:
    """The folded `article:*` properties."""

    published_time: datetime | str | None = None
    modified_time: datetime | str | None = None
    authors: tuple[str, ...] = ()
    section: Text | None = None
    tags: tuple[Text, ...] = ()


@dataclass(frozen=True, slots=True)
class OpenGraph:
    """The folded Open Graph block."""

    title: Text | None = None
    description: Text | None = None
    url: str | None = None
    type: str | None = None
    site_name: Text | None = None
    locale: str | None = None
    images: tuple[OpenGraphImage, ...] = ()
    article: Article | None = None


@dataclass(frozen=True, slots=True)
class Twitter:
    """The folded Twitter card block."""

    card: str | None = None
    site: str | None = None
    creator: str | None = None
    title: Text | None = None
    description: Text | None = None
    images: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Alternates:
    """The folded hreflang alternates."""

    languages: Mapping[str, str] | bool | None = None
    x_default: str | None = None


@dataclass(frozen=True, slots=True)
class Verification:
    """The folded verification tokens."""

    google: tuple[str, ...] = ()
    yandex: tuple[str, ...] = ()
    bing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Metadata:
    """The metadata of one page after the whole chain has been folded."""

    title: Text | None = None
    description: Text | None = None
    base: str | None = None
    site_name: Text | None = None
    canonical: str | bool | None = None
    alternates: Alternates | None = None
    robots: Robots | str | None = None
    og: OpenGraph | None = None
    twitter: Twitter | None = None
    verification: Verification | None = None
    other: tuple[tuple[str, Text], ...] = ()
    jsonld: tuple[Mapping[str, object], ...] = ()


EMPTY_METADATA: Final = Metadata()
"""The value a page without any metadata folds to."""


@dataclass(frozen=True, slots=True)
class TitleSpec:
    """One segment's title before the chain template has been applied."""

    text: Text | None = None
    template: Text | None = None
    default: Text | None = None
    absolute: Text | None = None


@dataclass(frozen=True, slots=True)
class Segment:
    """One normalised metadata source, named by `source` for the errors it raises."""

    source: str
    title: TitleSpec | None = None
    description: Text | None = None
    base: str | None = None
    site_name: Text | None = None
    canonical: str | bool | None = None
    alternates: Alternates | None = None
    robots: Robots | str | None = None
    og: OpenGraph | None = None
    twitter: Twitter | None = None
    verification: Verification | None = None
    other: tuple[tuple[str, Text], ...] = ()
    jsonld: tuple[Mapping[str, object], ...] = ()


class _Kind(Protocol):
    """One accepted shape of a metadata value."""

    @property
    def label(self) -> str: ...

    def coerce(self, value: object, *, source: str, path: str) -> object: ...


class _Option(_Kind, Protocol):
    """A shape that can also say cheaply whether a value is its own."""

    def accepts(self, value: object) -> bool: ...


def _is_text(value: object) -> bool:
    return isinstance(value, str | Promise)


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _same(value: object) -> object:
    return value


def _tuple_of_one(value: object) -> tuple[object, ...]:
    return (value,)


def _key_phrase(path: str) -> str:
    return f"metadata key {path!r}" if path else "metadata"


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _wrong_type(kind: _Kind, value: object, *, source: str, path: str) -> Never:
    detail = (
        f"declares {_key_phrase(path)} as {type(value).__name__!r}, "
        f"expected {kind.label}"
    )
    raise PageMetadataShapeError(source, detail)


@dataclass(frozen=True, slots=True)
class _Leaf:
    """A scalar shape decided by one predicate."""

    label: str
    test: Callable[[object], bool]
    convert: Callable[[Any], object] = _same

    def accepts(self, value: object) -> bool:
        return self.test(value)

    def coerce(self, value: object, *, source: str, path: str) -> object:
        if not self.test(value):
            _wrong_type(self, value, source=source, path=path)
        return self.convert(value)


@dataclass(frozen=True, slots=True)
class _Items:
    """A sequence whose items all share one shape."""

    item: _Kind

    @property
    def label(self) -> str:
        return f"a sequence where each item is {self.item.label}"

    def accepts(self, value: object) -> bool:
        return _is_sequence(value)

    def coerce(self, value: object, *, source: str, path: str) -> object:
        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            _wrong_type(self, value, source=source, path=path)
        return tuple(
            self.item.coerce(item, source=source, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )


@dataclass(frozen=True, slots=True)
class _Either:
    """The first of several shapes the value fits."""

    options: tuple[_Option, ...]

    @property
    def label(self) -> str:
        return " or ".join(option.label for option in self.options)

    def coerce(self, value: object, *, source: str, path: str) -> object:
        for option in self.options:
            if option.accepts(value):
                return option.coerce(value, source=source, path=path)
        _wrong_type(self, value, source=source, path=path)


@dataclass(frozen=True, slots=True)
class _Block[T]:
    """A mapping of known keys built into one frozen dataclass."""

    fields: Mapping[str, _Kind]
    build: Callable[..., T]

    @property
    def label(self) -> str:
        return "a mapping"

    def accepts(self, value: object) -> bool:
        return _is_mapping(value)

    def coerce(self, value: object, *, source: str, path: str) -> T:
        if not isinstance(value, Mapping):
            _wrong_type(self, value, source=source, path=path)
        checked: dict[str, Any] = {}
        for key, item in value.items():
            kind = self.fields.get(key)
            if kind is None:
                allowed = ", ".join(sorted(self.fields))
                detail = (
                    f"declares {_key_phrase(_child(path, str(key)))}, "
                    f"expected one of {allowed}"
                )
                raise PageMetadataShapeError(source, detail)
            if item is not None:
                checked[key] = kind.coerce(item, source=source, path=_child(path, key))
        return self.build(**checked)


def _str_pairs(value: object) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    )


def _text_or_texts(value: object) -> bool:
    if _is_text(value):
        return True
    return (
        isinstance(value, Sequence)
        and not isinstance(value, bytes)
        and all(map(_is_text, value))
    )


def _other_pairs(value: object) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and _text_or_texts(item) for key, item in value.items()
    )


def _fan_out(value: Mapping[str, Any]) -> tuple[tuple[str, Text], ...]:
    pairs: list[tuple[str, Text]] = []
    for key, item in value.items():
        if _is_text(item):
            pairs.append((key, item))
        else:
            pairs.extend((key, text) for text in item)
    return tuple(pairs)


def _bare_title(value: Text) -> TitleSpec:
    return TitleSpec(text=value)


def _bare_image(value: str) -> OpenGraphImage:
    return OpenGraphImage(url=value)


_TEXT = _Leaf("text", _is_text)
_STR = _Leaf("a string", _is_str)
_BOOL = _Leaf("a bool", _is_bool)
_INT = _Leaf("an int", _is_int)
_MAPPING = _Leaf("a mapping", _is_mapping)
_STR_OR_BOOL = _Leaf("a string or a bool", lambda value: isinstance(value, str | bool))
_DATETIME_OR_STR = _Leaf(
    "a datetime or a string", lambda value: isinstance(value, datetime | str)
)
_STR_ITEMS = _Items(_STR)
_TEXT_ITEMS = _Items(_TEXT)
_STR_OR_ITEMS = _Either((_Leaf("a string", _is_str, _tuple_of_one), _STR_ITEMS))
_LANGUAGES = _Either((_BOOL, _Leaf("a mapping of language codes to URLs", _str_pairs)))
_OTHER = _Leaf(
    "a mapping of names to text or sequences of text", _other_pairs, _fan_out
)
_JSONLD = _Either((_Leaf("a mapping", _is_mapping, _tuple_of_one), _Items(_MAPPING)))

_TITLE = _Block({"template": _TEXT, "default": _TEXT, "absolute": _TEXT}, TitleSpec)
_SITE_TITLE = _Block({"template": _TEXT, "default": _TEXT}, TitleSpec)
_BARE_TITLE = _Leaf("text", _is_text, _bare_title)
_ROBOTS_FLAGS: dict[str, _Kind] = {
    "index": _BOOL,
    "follow": _BOOL,
    "noarchive": _BOOL,
    "nosnippet": _BOOL,
    "noimageindex": _BOOL,
    "notranslate": _BOOL,
    "unavailable_after": _STR,
    "max_snippet": _INT,
    "max_image_preview": _STR,
    "max_video_preview": _INT,
}
_GOOGLEBOT = _Either((_STR, _Block(_ROBOTS_FLAGS, Robots)))
_ROBOTS = _Either((_STR, _Block({**_ROBOTS_FLAGS, "googlebot": _GOOGLEBOT}, Robots)))
_IMAGE = _Either(
    (
        _Leaf("a string", _is_str, _bare_image),
        _Block(
            {"url": _STR, "width": _INT, "height": _INT, "alt": _TEXT}, OpenGraphImage
        ),
    )
)
_ARTICLE = _Block(
    {
        "published_time": _DATETIME_OR_STR,
        "modified_time": _DATETIME_OR_STR,
        "authors": _STR_ITEMS,
        "section": _TEXT,
        "tags": _TEXT_ITEMS,
    },
    Article,
)
_OG = _Block(
    {
        "title": _TEXT,
        "description": _TEXT,
        "url": _STR,
        "type": _STR,
        "site_name": _TEXT,
        "locale": _STR,
        "images": _Items(_IMAGE),
        "article": _ARTICLE,
    },
    OpenGraph,
)
_TWITTER = _Block(
    {
        "card": _STR,
        "site": _STR,
        "creator": _STR,
        "title": _TEXT,
        "description": _TEXT,
        "images": _STR_ITEMS,
    },
    Twitter,
)
_ALTERNATES = _Block({"languages": _LANGUAGES, "x_default": _STR}, Alternates)
_VERIFICATION = _Block(
    {"google": _STR_OR_ITEMS, "yandex": _STR_OR_ITEMS, "bing": _STR_OR_ITEMS},
    Verification,
)
_SHARED_FIELDS: dict[str, _Kind] = {
    "description": _TEXT,
    "base": _STR,
    "site_name": _TEXT,
    "canonical": _STR_OR_BOOL,
    "alternates": _ALTERNATES,
    "robots": _ROBOTS,
    "og": _OG,
    "twitter": _TWITTER,
    "verification": _VERIFICATION,
    "other": _OTHER,
    "jsonld": _JSONLD,
}
_PAGE_FIELDS: dict[str, _Kind] = {
    **_SHARED_FIELDS,
    "title": _Either((_BARE_TITLE, _TITLE)),
}
_SITE_FIELDS: dict[str, _Kind] = {**_SHARED_FIELDS, "title": _SITE_TITLE}


def normalize_metadata(raw: object, *, source: str, site: bool = False) -> Segment:
    """Validate one raw metadata mapping strictly and return it as a `Segment`.

    A `Promise` counts as text and is never evaluated, so a lazy translation survives
    into the process memo and resolves under the language of each request.
    """
    fields = _SITE_FIELDS if site else _PAGE_FIELDS
    block = _Block(fields, functools.partial(Segment, source))
    return block.coerce(raw, source=source, path="")


__all__ = [
    "EMPTY_METADATA",
    "Alternates",
    "AlternatesDict",
    "Article",
    "ArticleDict",
    "Metadata",
    "MetadataDict",
    "OpenGraph",
    "OpenGraphDict",
    "OpenGraphImage",
    "OpenGraphImageDict",
    "Robots",
    "RobotsDict",
    "Segment",
    "SiteMetadataDict",
    "SiteTitleDict",
    "Text",
    "TitleDict",
    "TitleSpec",
    "Twitter",
    "TwitterDict",
    "Verification",
    "VerificationDict",
    "normalize_metadata",
]
