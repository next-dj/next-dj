from catalog.landing import FAQ, LEAD_ZONE, faq_node, landing_offers
from catalog.models import Category, Lead, Product
from catalog.queries import route_category
from django import forms
from django.http import HttpRequest, HttpResponse

from next import context, page
from next.forms import ComponentWidget, Form
from next.pages import CacheDict, MetadataDict
from next.partial import Patches
from next.urls import DUrl


cache: CacheDict = {"public": True, "max_age": 300, "stale_while_revalidate": 60}


class LaunchCodeForm(Form):
    """Sign a visitor up from the cached page, which embeds no CSRF token.

    The runtime fetches the token before it posts, so the form declares it needs one.
    """

    email = forms.EmailField(
        widget=ComponentWidget("input", type="email", placeholder="you@example.com")
    )

    class Meta:
        requires_runtime = True

    def on_valid(self, request: HttpRequest, category: DUrl[str]) -> HttpResponse:
        """Store the lead and thank the visitor in place of the form."""
        lead = Lead.objects.create(
            category=route_category(category), email=self.cleaned_data["email"]
        )
        patches = Patches(request).morph(zone=LEAD_ZONE, overrides={"lead": lead})
        return patches.response()


@context("category")
def category(category: str) -> Category:
    """Resolve the category the landing sells."""
    return route_category(category)


@context("offers")
def offers(category: Category) -> list[Product]:
    """Return the products the landing puts in front of the visitor."""
    return landing_offers(category)


@context("faq")
def faq() -> tuple[tuple[str, str], ...]:
    """Return the questions the landing answers under its offers."""
    return FAQ


@page.metadata
def landing_meta(category: Category) -> MetadataDict:
    """Describe the landing and publish its answers as structured data."""
    meta: MetadataDict = {
        "title": f"{category.name} deals",
        "canonical": True,
        "jsonld": [faq_node()],
    }
    if category.tagline:
        meta["description"] = category.tagline
    return meta
