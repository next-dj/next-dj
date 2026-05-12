from collections.abc import Mapping, Sequence

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse

from next.components import component


_HOVER_ACCENT = "hover:bg-accent hover:text-accent-foreground"

VARIANTS: dict[str, dict[str, str]] = {
    "tabs": {
        "base": (
            "inline-flex items-center justify-center rounded-md px-3 py-1.5"
            " text-sm transition-colors"
        ),
        "active": (
            "bg-background text-foreground font-semibold shadow-sm border border-border"
        ),
        "inactive": f"font-medium text-muted-foreground {_HOVER_ACCENT}",
    },
    "pills": {
        "base": (
            "inline-flex items-center rounded-full px-3 py-1.5 text-sm"
            " transition-colors"
        ),
        "active": "bg-primary text-primary-foreground font-semibold",
        "inactive": f"font-medium text-muted-foreground {_HOVER_ACCENT}",
    },
    "bar": {
        "base": "inline-flex items-center text-sm transition-colors",
        "active": "font-semibold text-foreground",
        "inactive": "font-medium text-muted-foreground hover:text-foreground",
    },
}


@component.context("href")
def href(
    url_name: str = "",
    url: str = "",
    url_kwargs: Mapping[str, object] | None = None,
    url_args: Sequence[object] | None = None,
) -> str:
    """Resolve the anchor target from ``url_name`` or a literal ``url``.

    Pass ``url_name`` for named routes; combine with ``url_kwargs`` or
    ``url_args`` when the URL pattern has converters. Pass ``url``
    directly for literal hrefs or values precomputed via
    ``{% url ... as %}``.
    """
    if url:
        return url
    if not url_name:
        return "#"
    try:
        if url_kwargs:
            return reverse(url_name, kwargs=dict(url_kwargs))
        if url_args:
            return reverse(url_name, args=list(url_args))
        return reverse(url_name)
    except NoReverseMatch:
        return "#"


@component.context("is_active")
def is_active(
    request: HttpRequest,
    url_name: str = "",
    active_when: str = "",
) -> bool:
    match = getattr(request, "resolver_match", None)
    if match is None:
        return False
    view_name = match.view_name
    if active_when:
        return active_when in view_name
    return view_name == url_name


@component.context("classes")
def classes(
    *,
    is_active: bool,
    variant: str = "tabs",
    extra: str = "",
) -> str:
    style = VARIANTS.get(variant, VARIANTS["tabs"])
    state = style["active"] if is_active else style["inactive"]
    parts = [style["base"], state]
    if extra:
        parts.append(extra)
    return " ".join(parts)
