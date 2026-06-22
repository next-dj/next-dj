import secrets
from pathlib import Path

from django import forms
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.template import Context, Template
from shortener.cache import pending_clicks
from shortener.models import Link

from next.forms import ComponentWidget, Form
from next.pages import context
from next.partial import Patches, is_partial_request


SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SLUG_ATTEMPTS_PER_LENGTH = 10
SLUG_MAX_LENGTH = 12

LATEST_LINKS_ZONE = "latest-links"
_TEMPLATE_PATH = Path(__file__).resolve().parent / "template.djx"
_ROW_TEMPLATE = Template('{% component "link_row" link=link %}')


class CreateLinkForm(Form):
    url = forms.URLField(
        max_length=2000,
        assume_scheme="https",
        widget=ComponentWidget(
            "input",
            type="url",
            placeholder="https://example.com/very/long/path",
        ),
    )

    class Meta:
        success_url = "/"
        success_message = "Short link created for %(url)s."

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Create a shortened link, then follow the declared success contract.

        A live runtime prepends the new row to the latest-links list with
        dedupe by slug so a resubmission replaces its row rather than
        doubling it. Without the runtime the form keeps its declared
        redirect so the no-JS path lands back on the home page.
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

    The page template path travels as `current_template_path` so the
    component resolver finds `link_row` next to the page, the same way
    the page render does.
    """
    return _ROW_TEMPLATE.render(
        Context(
            {
                "link": link,
                "request": request,
                "current_template_path": _TEMPLATE_PATH,
            }
        )
    )


@context("recent_links")
def recent_links() -> list[Link]:
    return list(Link.objects.all()[:5])


@context("pending_total_label")
def pending_total_label() -> str:
    """Label the badge zone with the live total of unflushed clicks."""
    total = sum(pending_clicks().values())
    return f"{total} pending clicks"


@context("flash_messages")
def flash_messages(request: HttpRequest) -> list[str]:
    """Drain the pending Meta.success_message flashes for the page banner."""
    return [str(m) for m in get_messages(request)]
