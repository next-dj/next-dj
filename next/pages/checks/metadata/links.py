"""The URL fields of a folded `Metadata` and the origin tests the checks run on them."""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from next.utils import WEB_SCHEMES


if TYPE_CHECKING:
    from collections.abc import Iterator

    from next.pages.metadata import Metadata
    from next.pages.metadata.dicts import Url


def _og_urls(meta: Metadata) -> Iterator[tuple[str, Url]]:
    """Yield the image, video and audio URL fields of the og block."""
    og = meta.og
    if og is None:
        return
    groups = (("images", og.images), ("videos", og.videos), ("audio", og.audio))
    for name, items in groups:
        for index, item in enumerate(items):
            for key in ("url", "secure_url"):
                url = getattr(item, key)
                if url is not None:
                    yield f"og.{name}[{index}].{key}", url


def _media_urls(meta: Metadata) -> Iterator[tuple[str, Url]]:
    """Yield the media and icon URL fields of a fold with their dotted names."""
    yield from _og_urls(meta)
    twitter = meta.twitter
    if twitter is not None:
        for index, image in enumerate(twitter.images):
            yield f"twitter.images[{index}].url", image.url
        player = twitter.player
        if player is not None:
            yield "twitter.player.url", player.url
            if player.stream is not None:
                yield "twitter.player.stream", player.stream
    for index, icon in enumerate(meta.icons):
        yield f"icons[{index}].url", icon.url


def _link_urls(meta: Metadata) -> Iterator[tuple[str, Url]]:
    """Yield the link URL fields of a fold with their dotted names."""
    if isinstance(meta.canonical, str):
        yield "canonical", meta.canonical
    if meta.og is not None and meta.og.url is not None:
        yield "og.url", meta.og.url
    if meta.manifest is not None:
        yield "manifest", meta.manifest
    for index, link in enumerate(meta.links):
        yield f"links[{index}].href", link.href
    alternates = meta.alternates
    if alternates is None:
        return
    if alternates.x_default is not None:
        yield "alternates.x_default", alternates.x_default
    if isinstance(alternates.languages, tuple):
        for code, url in alternates.languages:
            yield f"alternates.languages.{code}", url
    for index, feed in enumerate(alternates.feeds):
        yield f"alternates.feeds[{index}].url", feed.url


def url_fields(meta: Metadata) -> Iterator[tuple[str, str]]:
    """Yield every plain URL field of a fold, the links first, with its dotted name.

    A lazy URL is left out, the render that forces it checks it in its place.
    """
    for field, url in chain(_link_urls(meta), _media_urls(meta)):
        if isinstance(url, str):
            yield field, url


def root_relative(url: str) -> bool:
    """Whether `url` is a path from the host root, not a scheme-relative URL."""
    return url.startswith("/") and not url.startswith("//")


def _is_absolute(url: str) -> bool:
    """Whether `url` carries an http or https scheme."""
    return urlsplit(url).scheme in WEB_SCHEMES


def foreign_origin(url: str, base: str | None) -> bool:
    """Whether an absolute `url` sits on another host than the site URL `base`."""
    if not _is_absolute(url):
        return False
    return base is None or urlsplit(url).netloc != urlsplit(base).netloc


def same_origin(url: str, base: str | None) -> bool:
    """Whether `url` points at the origin of `base`, a root-relative path included."""
    if root_relative(url):
        return True
    return _is_absolute(url) and not foreign_origin(url, base)


__all__ = ["foreign_origin", "root_relative", "same_origin", "url_fields"]
