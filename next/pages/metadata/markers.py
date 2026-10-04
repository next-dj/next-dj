"""The frozen value objects of page metadata and the merge strategy of each field.

Annotations stay strings here because `dicts.py` imports `Replace` from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, Never, override


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date

    from .dicts import Text, Url
    from .ld import Node


MERGE_KEY: Final = "next_merge"
"""The dataclass field metadata key that names the merge strategy of the field."""

NOINDEX_DIRECTIVES: Final = frozenset({"noindex", "none"})
"""The robots directives that keep a page out of the index."""


class Merge(Enum):
    """How a segment merges one field over the value its ancestors folded to."""

    REPLACE = "replace"
    DEEP = "deep"
    BY_NAME = "by_name"
    BY_ID = "by_id"


_DEEP: Final = MappingProxyType({MERGE_KEY: Merge.DEEP})
_BY_NAME: Final = MappingProxyType({MERGE_KEY: Merge.BY_NAME})
_BY_ID: Final = MappingProxyType({MERGE_KEY: Merge.BY_ID})


@dataclass(frozen=True, slots=True)
class Replace[T]:
    """Take `value` whole instead of merging it, `None` dropping the inherited one."""

    value: T | None = None


RESET: Final[Replace[Never]] = Replace()
"""Drop the inherited value of a key, at any depth of the metadata."""


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
    googlebot: Robots | str | None = field(default=None, metadata=_DEEP)


def robots_directives(value: str) -> frozenset[str]:
    """Return the directive names of one robots value, lower-cased, agents dropped."""
    return frozenset(
        token.rsplit(":", 1)[-1].strip().lower() for token in value.split(",")
    )


def robots_noindex(robots: Robots | str | None) -> bool:
    """Whether the directives keep a page out of the index, `none` included."""
    if isinstance(robots, Robots):
        return robots.index is False
    if robots is None:
        return False
    return not robots_directives(robots).isdisjoint(NOINDEX_DIRECTIVES)


@dataclass(frozen=True, slots=True)
class OpenGraphImage:
    """One folded Open Graph image."""

    url: Url
    secure_url: Url | None = None
    type: str | None = None
    width: int | None = None
    height: int | None = None
    alt: Text | None = None


@dataclass(frozen=True, slots=True)
class OpenGraphVideo:
    """One folded Open Graph video."""

    url: Url
    secure_url: Url | None = None
    type: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class OpenGraphAudio:
    """One folded Open Graph audio track."""

    url: Url
    secure_url: Url | None = None
    type: str | None = None


@dataclass(frozen=True, slots=True)
class Profile:
    """The folded `profile:*` properties."""

    first_name: Text | None = None
    last_name: Text | None = None
    username: str | None = None
    gender: str | None = None


@dataclass(frozen=True, slots=True)
class Book:
    """The folded `book:*` properties."""

    authors: tuple[str, ...] = ()
    isbn: str | None = None
    release_date: date | str | None = None
    tags: tuple[Text, ...] = ()


@dataclass(frozen=True, slots=True)
class Article:
    """The folded `article:*` properties."""

    published_time: date | str | None = None
    modified_time: date | str | None = None
    authors: tuple[str, ...] = ()
    section: Text | None = None
    tags: tuple[Text, ...] = ()


@dataclass(frozen=True, slots=True)
class OpenGraph:
    """The folded Open Graph block."""

    title: Text | None = None
    description: Text | None = None
    url: Url | None = None
    type: str | None = None
    site_name: Text | None = None
    locale: str | None = None
    locale_alternates: tuple[str, ...] | bool = ()
    determiner: str | None = None
    images: tuple[OpenGraphImage, ...] = ()
    videos: tuple[OpenGraphVideo, ...] = ()
    audio: tuple[OpenGraphAudio, ...] = ()
    article: Article | None = field(default=None, metadata=_DEEP)
    profile: Profile | None = field(default=None, metadata=_DEEP)
    book: Book | None = field(default=None, metadata=_DEEP)


@dataclass(frozen=True, slots=True)
class TwitterImage:
    """One folded Twitter card image."""

    url: Url
    alt: Text | None = None


@dataclass(frozen=True, slots=True)
class TwitterPlayer:
    """The player of a Twitter `player` card."""

    url: Url
    width: int
    height: int
    stream: Url | None = None


@dataclass(frozen=True, slots=True)
class Twitter:
    """The folded Twitter card block."""

    card: str | None = None
    site: str | None = None
    site_id: str | None = None
    creator: str | None = None
    creator_id: str | None = None
    title: Text | None = None
    description: Text | None = None
    images: tuple[TwitterImage, ...] = ()
    player: TwitterPlayer | None = None


@dataclass(frozen=True, slots=True)
class Feed:
    """One RSS, Atom or JSON feed of the page."""

    url: Url
    type: str
    title: Text | None = None


@dataclass(frozen=True, slots=True)
class Alternates:
    """The folded hreflang alternates and feeds, each language a `(code, url)` pair."""

    languages: tuple[tuple[str, Url], ...] | bool | None = None
    x_default: Url | None = None
    feeds: tuple[Feed, ...] = ()


@dataclass(frozen=True, slots=True)
class Verification:
    """The folded verification tokens, `other` holding the engines without a key."""

    google: tuple[str, ...] = ()
    yandex: tuple[str, ...] = ()
    bing: tuple[str, ...] = ()
    pinterest: tuple[str, ...] = ()
    facebook: tuple[str, ...] = ()
    other: tuple[tuple[str, Text], ...] = field(default=(), metadata=_BY_NAME)


@dataclass(frozen=True, slots=True)
class Icon:
    """One icon link, the rel of the `icons` group it came from."""

    rel: str
    url: Url
    sizes: str | None = None
    type: str | None = None
    media: str | None = None
    color: str | None = None


@dataclass(frozen=True, slots=True)
class Link:
    """One free-form `<link>`, its attributes past `rel` and `href` in order."""

    rel: str
    href: Url
    attrs: tuple[tuple[str, Text], ...] = ()


@dataclass(frozen=True, slots=True)
class Viewport:
    """The viewport directives."""

    width: str | int | None = None
    height: str | int | None = None
    initial_scale: float | None = None
    minimum_scale: float | None = None
    maximum_scale: float | None = None
    user_scalable: bool | None = None
    viewport_fit: str | None = None
    interactive_widget: str | None = None


@dataclass(frozen=True, slots=True)
class ThemeColor:
    """One theme color, for the media query it names or for every one."""

    color: str
    media: str | None = None


@dataclass(frozen=True, slots=True)
class Crumb:
    """One breadcrumb of the fold, the label of a segment and its directory trail."""

    label: Text
    trail: str


@dataclass(frozen=True, slots=True)
class Breadcrumb:
    """One resolved breadcrumb, `url` absent where its page does not reverse."""

    label: Text
    url: str | None
    current: bool = False


class Breadcrumbs:
    """The resolved crumbs of one response, built on the first read and kept."""

    __slots__ = ("_build", "_items")

    def __init__(self, build: Callable[[], tuple[Breadcrumb, ...]]) -> None:
        """Hold the build of the crumbs without running it."""
        self._build = build
        self._items: tuple[Breadcrumb, ...] | None = None

    def items(self) -> tuple[Breadcrumb, ...]:
        """Return the crumbs, the build running on the first call alone."""
        items = self._items
        if items is None:
            items = self._items = self._build()
        return items

    @override
    def __eq__(self, other: object) -> bool:
        """Compare the crumbs themselves, whichever build made them."""
        if not isinstance(other, Breadcrumbs):
            return NotImplemented
        return self.items() == other.items()

    @override
    def __hash__(self) -> int:
        """Hash the crumbs themselves."""
        return hash(self.items())

    @override
    def __repr__(self) -> str:
        """Show the crumbs themselves."""
        return f"Breadcrumbs({self.items()!r})"


NO_BREADCRUMBS: Final = Breadcrumbs(tuple)
"""The crumbs of a response whose fold declares none."""


@dataclass(frozen=True, slots=True)
class Metadata:
    """The metadata of one page after the whole chain has been folded."""

    title: Text | None = None
    description: Text | None = None
    site_name: Text | None = None
    canonical: Url | Literal[True] | None = None
    alternates: Alternates | None = field(default=None, metadata=_DEEP)
    robots: Robots | str | None = field(default=None, metadata=_DEEP)
    og: OpenGraph | None = field(default=None, metadata=_DEEP)
    twitter: Twitter | None = field(default=None, metadata=_DEEP)
    verification: Verification | None = field(default=None, metadata=_DEEP)
    other: tuple[tuple[str, Text], ...] = field(default=(), metadata=_BY_NAME)
    jsonld: tuple[Node | Mapping[str, object], ...] = field(default=(), metadata=_BY_ID)
    keywords: tuple[Text, ...] = ()
    icons: tuple[Icon, ...] = ()
    manifest: Url | None = None
    links: tuple[Link, ...] = ()
    properties: tuple[tuple[str, Text], ...] = field(default=(), metadata=_BY_NAME)
    viewport: Viewport | str | None = None
    theme_color: tuple[ThemeColor, ...] = ()
    color_scheme: str | None = None
    breadcrumbs: tuple[Crumb, ...] = ()

    @property
    def noindex(self) -> bool:
        """Whether the robots directives keep the page out of the index."""
        return robots_noindex(self.robots)


_EMPTY: Final = Metadata()


@dataclass(frozen=True, slots=True)
class TitleSpec:
    """One segment's title before the chain template has been applied."""

    text: Text | None = None
    template: Text | None = None
    default: Text | None = None
    absolute: Text | None = None


@dataclass(frozen=True, slots=True)
class Segment:
    """One normalised source, its `metadata` and the keys folded outside of it.

    `replaced` holds the key paths a `Replace` set whole, and `trail` the directory.
    """

    source: str
    metadata: Metadata = _EMPTY
    title: TitleSpec | None = None
    breadcrumb: Text | Literal[False] | None = None
    trail: str | None = None
    replaced: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ResolvedMetadata:
    """The folded metadata of one response, every URL absolute and every policy applied.

    `og` carries its fallbacks, `jsonld` the graph nodes and `source` its fold.
    """

    title: Text | None
    description: Text | None
    noindex: bool
    robots: str | None
    googlebot: str | None
    canonical: str | None
    alternates: tuple[tuple[str, str], ...]
    verification: tuple[tuple[str, Text], ...]
    other: tuple[tuple[str, Text], ...]
    og: OpenGraph | None
    twitter: Twitter | None
    jsonld: tuple[Mapping[str, object], ...]
    source: Metadata
    keywords: tuple[Text, ...] = ()
    viewport: str | None = None
    theme_color: tuple[ThemeColor, ...] = ()
    color_scheme: str | None = None
    feeds: tuple[Feed, ...] = ()
    icons: tuple[Icon, ...] = ()
    manifest: str | None = None
    links: tuple[Link, ...] = ()
    properties: tuple[tuple[str, Text], ...] = ()
    crumbs: Breadcrumbs = NO_BREADCRUMBS

    @property
    def breadcrumbs(self) -> tuple[Breadcrumb, ...]:
        """Return the crumbs of the page, their URLs reversed on the first read."""
        return self.crumbs.items()


__all__ = [
    "MERGE_KEY",
    "NOINDEX_DIRECTIVES",
    "NO_BREADCRUMBS",
    "RESET",
    "Alternates",
    "Article",
    "Book",
    "Breadcrumb",
    "Breadcrumbs",
    "Crumb",
    "Feed",
    "Icon",
    "Link",
    "Merge",
    "Metadata",
    "OpenGraph",
    "OpenGraphAudio",
    "OpenGraphImage",
    "OpenGraphVideo",
    "Profile",
    "Replace",
    "ResolvedMetadata",
    "Robots",
    "Segment",
    "ThemeColor",
    "TitleSpec",
    "Twitter",
    "TwitterImage",
    "TwitterPlayer",
    "Verification",
    "Viewport",
    "robots_directives",
    "robots_noindex",
]
