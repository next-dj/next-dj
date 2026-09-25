"""The URL fields of a folded `Metadata` and the origin tests the checks run on them."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from next.utils import WEB_SCHEMES


if TYPE_CHECKING:
    from collections.abc import Iterator

    from next.pages.metadata import Metadata


def _image_urls(meta: Metadata) -> Iterator[tuple[str, str]]:
    """Yield the image URL fields of a fold with their dotted names."""
    if meta.og is not None:
        for index, image in enumerate(meta.og.images):
            if image.url is not None:
                yield f"og.images[{index}].url", image.url
    if meta.twitter is not None:
        for index, url in enumerate(meta.twitter.images):
            yield f"twitter.images[{index}]", url


def _link_urls(meta: Metadata) -> Iterator[tuple[str, str]]:
    """Yield the link URL fields of a fold with their dotted names."""
    if isinstance(meta.canonical, str):
        yield "canonical", meta.canonical
    if meta.og is not None and meta.og.url is not None:
        yield "og.url", meta.og.url
    alternates = meta.alternates
    if alternates is None:
        return
    if alternates.x_default is not None:
        yield "alternates.x_default", alternates.x_default
    if isinstance(alternates.languages, Mapping):
        for code, url in alternates.languages.items():
            yield f"alternates.languages.{code}", url


def url_fields(meta: Metadata) -> Iterator[tuple[str, str]]:
    """Yield every URL field of a fold, the links first, with its dotted name."""
    yield from _link_urls(meta)
    yield from _image_urls(meta)


def root_relative(url: str) -> bool:
    """Whether `url` is a path from the host root, not a scheme-relative URL."""
    return url.startswith("/") and not url.startswith("//")


def _is_absolute(url: str) -> bool:
    """Whether `url` carries an http or https scheme."""
    return urlsplit(url).scheme in WEB_SCHEMES


def is_origin(url: str) -> bool:
    """Whether `url` is a bare http or https origin, a host with nothing after it."""
    parts = urlsplit(url)
    return (
        parts.scheme in WEB_SCHEMES
        and bool(parts.netloc)
        and parts.path in {"", "/"}
        and not parts.query
        and not parts.fragment
    )


def foreign_origin(url: str, base: str | None) -> bool:
    """Whether an absolute `url` sits on another host than `base`, or `base` is None."""
    if not _is_absolute(url):
        return False
    return base is None or urlsplit(url).netloc != urlsplit(base).netloc


def same_origin(url: str, base: str | None) -> bool:
    """Whether `url` points at the origin of `base`, a root-relative path included."""
    if root_relative(url):
        return True
    return _is_absolute(url) and not foreign_origin(url, base)


__all__ = ["foreign_origin", "is_origin", "root_relative", "same_origin", "url_fields"]
