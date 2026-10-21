import secrets

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum

from shortener.models import Link


SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SLUG_ATTEMPTS_PER_LENGTH = 10
SLUG_MAX_LENGTH = 12


def _random_slug(length: int) -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(length))


def create_link(*, url: str, length: int = 6) -> Link:
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


def delete_link(*, slug: str) -> bool:
    """Delete the link `slug` names and return whether there was one."""
    deleted, _ = Link.objects.filter(slug=slug).delete()
    return bool(deleted)


def find_link(slug: str) -> Link | None:
    """Return the link `slug` names, or `None`."""
    return Link.objects.filter(slug=slug).first()


def latest_links(limit: int) -> list[Link]:
    """Return the `limit` newest links."""
    return list(Link.objects.all()[:limit])


def top_links(limit: int) -> list[Link]:
    """Return the `limit` most clicked links, the newer of two equal counts first."""
    return list(Link.objects.order_by("-clicks", "-created_at")[:limit])


def link_totals() -> dict[str, int]:
    """Return how many links exist and how many clicks the database holds for them."""
    aggregate = Link.objects.aggregate(
        link_count=Count("id"), total_clicks=Sum("clicks")
    )
    return {
        "links": aggregate["link_count"],
        "clicks": int(aggregate["total_clicks"] or 0),
    }
