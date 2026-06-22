from django.http import HttpRequest, HttpResponse
from shortener.cache import CLICK_PREFIX, reset_clicks
from shortener.models import Link
from shortener.providers import DLink

from next.forms import action
from next.pages import context
from next.partial import Patches


@context
def link_context(link: DLink[Link]) -> dict[str, object]:
    return {
        "link": link,
        "cache_key": f"{CLICK_PREFIX}{link.slug}",
    }


@action("reset_clicks")
def reset_link_clicks(request: HttpRequest, slug: str) -> HttpResponse:
    """Reset this link's cached clicks and refresh the home badge out of band.

    The home page owns the `links-badge` zone that totals unflushed
    clicks. Resetting from the detail page morphs that foreign zone out
    of band so an open home tab updates without its own request. Without
    the runtime the builder falls back to a redirect to this detail page.
    """
    reset_clicks(slug)
    return (
        Patches(request)
        .morph_foreign_zone("links-badge", "/")
        .response(fallback=f"/admin/links/{slug}/")
    )
