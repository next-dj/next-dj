"""The scheme and domain of the absolute URLs in the SEO responses.

The configured site URL takes precedence over the sites framework and the `Host` header.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.contrib.sites.requests import RequestSite

from next.site import site_origin


if TYPE_CHECKING:
    from django.http import HttpRequest


class OriginSite(RequestSite):
    """A `RequestSite` built from a known domain instead of from a request."""

    def __init__(self, domain: str) -> None:
        """Set `domain` as both the domain and the name of the site."""
        self.domain = self.name = domain


@dataclass(frozen=True, slots=True)
class Origin:
    """The scheme and domain of the absolute URLs in one response."""

    scheme: str
    domain: str

    @property
    def site(self) -> OriginSite:
        """Return the domain wrapped in the site object Django sitemaps read."""
        return OriginSite(self.domain)

    def url(self, path: str) -> str:
        """Return `path` made absolute on the origin."""
        return f"{self.scheme}://{self.domain}{path}"


def request_origin(request: HttpRequest | None) -> Origin:
    """Return the origin of the site URL, else of the current site of `request`.

    Without a request only the site URL applies, and `SiteOriginError` is raised
    when it is not set.
    """
    return Origin(*site_origin(request))


__all__ = ["Origin", "OriginSite", "request_origin"]
