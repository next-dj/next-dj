"""The middleware that closes the responses the framework does not build itself."""

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.deprecation import MiddlewareMixin

from .headers import stamp_site_robots


class RobotsHeaderMiddleware(MiddlewareMixin):
    """Stamp `X-Robots-Tag: noindex, nofollow` on every response of a closed site.

    Pages and the framework routes carry it already, this reaches admin, API and media.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """Return `response`, closed to search when the site is."""
        return stamp_site_robots(response, request)


__all__ = ["RobotsHeaderMiddleware"]
