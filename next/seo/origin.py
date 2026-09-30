"""The scheme and the domain the SEO routes make their URLs absolute on.

The site URL wins, and only without it the sites framework or `Host` answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.contrib.sites.requests import RequestSite

from next.site import site_origin


if TYPE_CHECKING:
    from django.http import HttpRequest


class OriginSite(RequestSite):
    """A `RequestSite` spelled from a known domain instead of read off a request."""

    def __init__(self, domain: str) -> None:
        """Carry `domain` as both the domain and the name of the site."""
        self.domain = self.name = domain


@dataclass(frozen=True, slots=True)
class Origin:
    """The scheme and the domain one response writes its absolute URLs on."""

    scheme: str
    domain: str

    @property
    def site(self) -> OriginSite:
        """Return the domain as the site object a Django sitemap reads it from."""
        return OriginSite(self.domain)

    def url(self, path: str) -> str:
        """Return `path` made absolute on the origin."""
        return f"{self.scheme}://{self.domain}{path}"


def request_origin(request: HttpRequest | None) -> Origin:
    """Return the origin of the site URL, else of the current site of `request`.

    A request-free caller has only the site URL, so without one it raises.
    """
    return Origin(*site_origin(request))


__all__ = ["Origin", "OriginSite", "request_origin"]
