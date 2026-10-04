"""The middleware that sets the robots header on responses outside the framework."""

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.deprecation import MiddlewareMixin

from .headers import stamp_site_robots


class RobotsHeaderMiddleware(MiddlewareMixin):
    """Stamp `X-Robots-Tag: noindex, nofollow` on every response of a closed site.

    Pages and framework routes set it already. The middleware covers admin, API and
    media responses.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        """Return `response`, closed to search when the site is."""
        return stamp_site_robots(response, request)


__all__ = ["RobotsHeaderMiddleware"]
