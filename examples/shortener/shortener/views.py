from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponseRedirect

from .cache import increment_clicks
from .models import Link


def redirect_slug(request: HttpRequest, slug: str) -> HttpResponseRedirect:  # noqa: ARG001
    """Bump the click counter in LocMemCache and redirect to the target URL."""
    try:
        link = Link.objects.get(slug=slug)
    except Link.DoesNotExist as exc:
        raise Http404 from exc
    increment_clicks(slug)
    return HttpResponseRedirect(link.url)
