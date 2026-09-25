"""The metadata input contract and its immutable folded form."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, TypedDict

from django.utils.functional import Promise


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

    @property
    def noindex(self) -> bool:
        """Whether the robots directives keep the page out of the index."""
        robots = self.robots
        if isinstance(robots, Robots):
            return robots.index is False
        return isinstance(robots, str) and "noindex" in robots


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
]
