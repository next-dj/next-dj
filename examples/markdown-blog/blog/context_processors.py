from datetime import UTC, datetime

from django.http import HttpRequest


SITE_TAGLINE = "Small posts, plain Markdown, zero front-end build."


def site_nav(request: HttpRequest) -> dict[str, object]:
    """Supply site-wide chrome variables available to every template."""
    return {
        "site_tagline": SITE_TAGLINE,
        "site_year": datetime.now(tz=UTC).year,
        "site_path": request.path,
    }
