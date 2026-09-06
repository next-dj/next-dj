from urllib.parse import quote

from django.utils.safestring import SafeString

from next import component


DEFAULT_ICON = "▲"


@component.context("favicon_href")
def favicon_href(icon: str = DEFAULT_ICON) -> SafeString:
    """Draw the example's emoji as an inline SVG data URI for the tab icon.

    A project that ships no favicon logs a 404 on every page load, and an
    inline data URI keeps the examples free of binary assets.
    """
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
        f"<text y='.9em' font-size='90'>{icon}</text>"
        "</svg>"
    )
    return SafeString(quote(svg))
