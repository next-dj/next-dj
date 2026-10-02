"""The metadata input contract, the dicts a `page.py` and the settings declare."""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal, Required, TypedDict

from django.utils.functional import Promise

from .ld import Node
from .markers import Replace


type Text = str | Promise
type Url = str | Promise


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

    index: bool | Replace[bool]
    follow: bool | Replace[bool]
    noarchive: bool | Replace[bool]
    nosnippet: bool | Replace[bool]
    noimageindex: bool | Replace[bool]
    notranslate: bool | Replace[bool]
    unavailable_after: str | Replace[str]
    max_snippet: int | Replace[int]
    max_image_preview: str | Replace[str]
    max_video_preview: int | Replace[int]
    googlebot: "str | RobotsDict | Replace[str | RobotsDict]"


class OpenGraphImageDict(TypedDict, total=False):
    """One Open Graph image with its optional dimensions and alt text."""

    url: Required[Url]
    secure_url: Url
    type: str
    width: int
    height: int
    alt: Text


class OpenGraphVideoDict(TypedDict, total=False):
    """One Open Graph video."""

    url: Required[Url]
    secure_url: Url
    type: str
    width: int
    height: int


class OpenGraphAudioDict(TypedDict, total=False):
    """One Open Graph audio track."""

    url: Required[Url]
    secure_url: Url
    type: str


class ProfileDict(TypedDict, total=False):
    """The `profile:*` Open Graph properties."""

    first_name: Text | Replace[Text]
    last_name: Text | Replace[Text]
    username: str | Replace[str]
    gender: str | Replace[str]


class BookDict(TypedDict, total=False):
    """The `book:*` Open Graph properties."""

    authors: Sequence[str] | Replace[Sequence[str]]
    isbn: str | Replace[str]
    release_date: date | str | Replace[date | str]
    tags: Sequence[Text] | Replace[Sequence[Text]]


class ArticleDict(TypedDict, total=False):
    """The `article:*` Open Graph properties."""

    published_time: date | str | Replace[date | str]
    modified_time: date | str | Replace[date | str]
    authors: Sequence[str] | Replace[Sequence[str]]
    section: Text | Replace[Text]
    tags: Sequence[Text] | Replace[Sequence[Text]]


class OpenGraphDict(TypedDict, total=False):
    """The Open Graph block."""

    title: Text | Replace[Text]
    description: Text | Replace[Text]
    url: Url | Replace[Url]
    type: str | Replace[str]
    site_name: Text | Replace[Text]
    locale: str | Replace[str]
    locale_alternates: bool | Sequence[str] | Replace[bool | Sequence[str]]
    determiner: str | Replace[str]
    images: Sequence[Url | OpenGraphImageDict] | Replace[Sequence[Url]]
    videos: Sequence[Url | OpenGraphVideoDict] | Replace[Sequence[Url]]
    audio: Sequence[Url | OpenGraphAudioDict] | Replace[Sequence[Url]]
    article: ArticleDict | Replace[ArticleDict]
    profile: ProfileDict | Replace[ProfileDict]
    book: BookDict | Replace[BookDict]


class TwitterImageDict(TypedDict, total=False):
    """One Twitter card image with its alt text."""

    url: Required[Url]
    alt: Text


class TwitterPlayerDict(TypedDict, total=False):
    """The player of a Twitter `player` card."""

    url: Required[Url]
    width: Required[int]
    height: Required[int]
    stream: Url


class TwitterDict(TypedDict, total=False):
    """The Twitter card block."""

    card: str | Replace[str]
    site: str | Replace[str]
    site_id: str | Replace[str]
    creator: str | Replace[str]
    creator_id: str | Replace[str]
    title: Text | Replace[Text]
    description: Text | Replace[Text]
    images: Sequence[Url | TwitterImageDict] | Replace[Sequence[Url]]
    player: TwitterPlayerDict | Replace[TwitterPlayerDict]


class FeedDict(TypedDict, total=False):
    """One feed, its `type` being `rss`, `atom`, `json` or a media type."""

    url: Required[Url]
    type: Required[str]
    title: Text


class AlternatesDict(TypedDict, total=False):
    """The hreflang alternates, `x-default` in `languages` moving to `x_default`."""

    languages: Mapping[str, Url] | bool | Replace[Mapping[str, Url] | bool]
    x_default: Url | Replace[Url]
    feeds: Sequence[FeedDict] | Replace[Sequence[FeedDict]]


class VerificationDict(TypedDict, total=False):
    """Site verification tokens per search engine, `other` naming the meta itself."""

    google: str | Sequence[str] | Replace[str | Sequence[str]]
    yandex: str | Sequence[str] | Replace[str | Sequence[str]]
    bing: str | Sequence[str] | Replace[str | Sequence[str]]
    pinterest: str | Sequence[str] | Replace[str | Sequence[str]]
    facebook: str | Sequence[str] | Replace[str | Sequence[str]]
    other: Mapping[str, str | Sequence[str] | Replace[str | Sequence[str]]]


class IconDict(TypedDict, total=False):
    """One icon with its optional sizes, media type and media query."""

    url: Required[Url]
    sizes: str
    type: str
    media: str


class OtherIconDict(IconDict, total=False):
    """One icon of another rel, `mask-icon` with its `color` among them."""

    rel: Required[str]
    color: str


type IconList = Url | IconDict | Sequence[Url | IconDict]


class IconsDict(TypedDict, total=False):
    """The icon links, rendered in the order icon, apple, other."""

    icon: IconList
    apple: IconList
    other: Sequence[OtherIconDict]


LinkDict = TypedDict(
    "LinkDict",
    {
        "rel": Required[str],
        "href": Required[Url],
        "as": str,
        "type": str,
        "media": str,
        "sizes": str,
        "crossorigin": bool | str,
        "hreflang": str,
        "title": Text,
        "fetchpriority": str,
        "imagesrcset": str,
        "imagesizes": str,
        "referrerpolicy": str,
        "integrity": str,
        "blocking": str,
    },
    total=False,
)
"""One free-form `<link>`, in the functional syntax since `as` is a keyword."""


class ViewportDict(TypedDict, total=False):
    """The viewport directives, rendered into one `content`."""

    width: str | int
    height: str | int
    initial_scale: float
    minimum_scale: float
    maximum_scale: float
    user_scalable: bool
    viewport_fit: str
    interactive_widget: str


class ThemeColorDict(TypedDict, total=False):
    """One theme color, for one media query or for every one."""

    color: Required[str]
    media: str


type JsonLdItem = Node | Mapping[str, object]
type JsonLd = JsonLdItem | Sequence[JsonLdItem]


class _SharedMetadataDict(TypedDict, total=False):
    description: Text | Replace[Text]
    site_name: Text | Replace[Text]
    canonical: Url | Literal[True] | Replace[Url]
    alternates: AlternatesDict | Replace[AlternatesDict]
    robots: str | RobotsDict | Replace[str | RobotsDict]
    og: OpenGraphDict | Replace[OpenGraphDict]
    twitter: TwitterDict | Replace[TwitterDict]
    verification: VerificationDict | Replace[VerificationDict]
    other: Mapping[str, Text | Sequence[Text] | Replace[Text | Sequence[Text]]]
    jsonld: JsonLd | Replace[JsonLd]
    keywords: Text | Sequence[Text] | Replace[Text | Sequence[Text]]
    icons: IconsDict | Replace[IconsDict]
    manifest: Url | Replace[Url]
    links: Sequence[LinkDict] | Replace[Sequence[LinkDict]]
    properties: Mapping[str, Text | Sequence[Text] | Replace[Text | Sequence[Text]]]
    viewport: str | ViewportDict | Replace[str | ViewportDict]
    theme_color: (
        str | Sequence[ThemeColorDict] | Replace[str | Sequence[ThemeColorDict]]
    )
    color_scheme: str | Replace[str]


class MetadataDict(_SharedMetadataDict, total=False):
    """The metadata a `page.py` declares as a dict or returns from its callable."""

    title: Text | TitleDict | Replace[Text | TitleDict]
    breadcrumb: Text | Literal[False]


class SiteMetadataDict(_SharedMetadataDict, total=False):
    """The metadata `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` seeds the chain with."""

    title: SiteTitleDict


__all__ = [
    "AlternatesDict",
    "ArticleDict",
    "BookDict",
    "FeedDict",
    "IconDict",
    "IconsDict",
    "LinkDict",
    "MetadataDict",
    "OpenGraphAudioDict",
    "OpenGraphDict",
    "OpenGraphImageDict",
    "OpenGraphVideoDict",
    "OtherIconDict",
    "ProfileDict",
    "RobotsDict",
    "SiteMetadataDict",
    "SiteTitleDict",
    "Text",
    "ThemeColorDict",
    "TitleDict",
    "TwitterDict",
    "TwitterImageDict",
    "TwitterPlayerDict",
    "Url",
    "VerificationDict",
    "ViewportDict",
]
