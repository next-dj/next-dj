"""The origin test the metadata checks run on the URLs of a fold."""

from __future__ import annotations

from urllib.parse import urlsplit

from next.utils import WEB_SCHEMES


def foreign_origin(url: str, base: str | None) -> bool:
    """Whether an absolute `url` sits on another host than the site URL `base`."""
    parts = urlsplit(url)
    if parts.scheme not in WEB_SCHEMES:
        return False
    return base is None or parts.netloc != urlsplit(base).netloc


__all__ = ["foreign_origin"]
