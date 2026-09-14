import secrets
from pathlib import Path

from django import forms
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from fragments import render_fragment
from shortener.cache import pending_clicks
from shortener.models import Link

from next import context
from next.forms import ComponentWidget, Form
from next.partial import Patches, is_partial_request
from next.urls import page_reverse_lazy


SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SLUG_ATTEMPTS_PER_LENGTH = 10
SLUG_MAX_LENGTH = 12

LATEST_LINKS_ZONE = "latest-links"
BADGE_ZONE = "links-badge"
_TEMPLATE_PATH = Path(__file__).resolve().parent / "template.djx"


class CreateLinkForm(Form):
    url = forms.URLField(
        max_length=2000,
        assume_scheme="https",
        widget=ComponentWidget(
            "input", type="url", placeholder="https://example.com/very/long/path"
        ),
    )

    class Meta:
        success_url = page_reverse_lazy()
        success_message = "Short link created for %(url)s."

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Create a shortened link, then follow the declared success contract.

        A live runtime dedupes the new row by slug, replacing rather than doubling it.
        """
        link = _create_link_with_unique_slug(self.cleaned_data["url"])
        if is_partial_request(request):
            row = _render_row(link, request)
            return (
                Patches(request)
                .prepend({"zone": LATEST_LINKS_ZONE}, row, dedupe="key")
                .response()
            )
        return super().on_valid(request)


def _random_slug(length: int) -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(length))


def _create_link_with_unique_slug(url: str, length: int = 6) -> Link:
    """Create a `Link` by trying random slugs until one passes the unique constraint."""
    while length <= SLUG_MAX_LENGTH:
        for _ in range(SLUG_ATTEMPTS_PER_LENGTH):
            candidate = _random_slug(length)
            try:
                with transaction.atomic():
                    return Link.objects.create(slug=candidate, url=url)
            except IntegrityError:
                continue
        length += 1
    msg = f"Could not allocate a unique slug within {SLUG_MAX_LENGTH} characters"
    raise RuntimeError(msg)


def _render_row(link: Link, request: HttpRequest) -> str:
    """Render one keyed `link_row` for a prepend patch.

    The page template is the anchor, so `link_row` resolves next to the page.
    """
    return render_fragment("link_row", _TEMPLATE_PATH, request, link=link)


@context("recent_links", zone=LATEST_LINKS_ZONE)
def recent_links() -> list[Link]:
    return list(Link.objects.all()[:5])


@context("pending_total_label", zone=BADGE_ZONE)
def pending_total_label() -> str:
    """Label the badge zone with the live total of unflushed clicks.

    The `zone=` binding keeps the click-cache sum out of a `latest-links`
    render, while the badge morph the detail page aims here still gets it.
    """
    total = sum(pending_clicks().values())
    return f"{total} pending clicks"
