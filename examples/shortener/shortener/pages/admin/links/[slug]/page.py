from django.http import HttpRequest, HttpResponse
from shortener.cache import CLICK_PREFIX, reset_clicks
from shortener.deps import DLink
from shortener.models import Link

from next import action, context, page
from next.pages import MetadataDict
from next.partial import Patches


page.public()


@page.metadata
def link_meta(link: DLink[Link]) -> MetadataDict:
    """Title the detail tab after the slug the provider resolved."""
    return {"title": link.slug}


@context
def link_context(link: DLink[Link]) -> dict[str, object]:
    return {"link": link, "cache_key": f"{CLICK_PREFIX}{link.slug}"}


@action("reset_clicks")
def reset_link_clicks(request: HttpRequest, slug: str) -> HttpResponse:
    """Reset this link's cached clicks and refresh the home badge out of band.

    The detail page morphs the home `links-badge` zone, redirecting without a runtime.
    """
    reset_clicks(slug)
    return (
        Patches(request)
        .morph_foreign_zone("links-badge", "/")
        .response(fallback=f"/admin/links/{slug}/")
    )
