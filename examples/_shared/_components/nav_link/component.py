from collections.abc import Mapping, Sequence

from django.urls import NoReverseMatch, reverse

from next import component


_HOVER_ACCENT = "hover:bg-accent hover:text-accent-foreground"
_PAGE = "aria-[current=page]:"
_ANCESTOR = "data-[next-current=ancestor]:"


def _marked(prefix: str, *tokens: str) -> str:
    """Return each token behind the variant that matches one mark of the router."""
    return " ".join(prefix + token for token in tokens)


VARIANTS: dict[str, str] = {
    "tabs": " ".join(
        (
            "inline-flex items-center justify-center rounded-md border",
            "border-transparent px-3 py-1.5 text-sm font-medium",
            "text-muted-foreground transition-colors",
            _HOVER_ACCENT,
            _marked(
                _PAGE,
                "border-border",
                "bg-background",
                "font-semibold",
                "text-foreground",
                "shadow-sm",
            ),
        )
    ),
    "pills": " ".join(
        (
            "inline-flex items-center rounded-full px-3 py-1.5 text-sm font-medium",
            "text-muted-foreground transition-colors",
            _HOVER_ACCENT,
            _marked(_PAGE, "bg-primary", "font-semibold", "text-primary-foreground"),
        )
    ),
    "bar": " ".join(
        (
            "inline-flex items-center text-sm font-medium text-muted-foreground",
            "transition-colors hover:text-foreground",
            _marked(_PAGE, "font-semibold", "text-foreground"),
            _marked(_ANCESTOR, "font-semibold", "text-foreground"),
        )
    ),
}


@component.context("href")
def href(
    url_name: str = "",
    url: str = "",
    url_kwargs: Mapping[str, object] | None = None,
    url_args: Sequence[object] | None = None,
) -> str:
    """Resolve the anchor target from a literal ``url`` or ``url_name``, else ``#``."""
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


@component.context("classes")
def classes(variant: str = "tabs", extra: str = "") -> str:
    """Return the class string of `variant`, styled from the marks the router writes."""
    return f"{VARIANTS.get(variant, VARIANTS['tabs'])} {extra}".rstrip()
