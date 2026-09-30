"""SEO routes implementation bound into the `next.ports` slot at app startup."""

from typing import TYPE_CHECKING, override

from next.ports import SeoRoutes

from .routes import served_patterns
from .urls import PATTERNS


if TYPE_CHECKING:
    from django.urls import URLPattern


class SeoRoutesImpl(SeoRoutes):
    """Answer the SEO routes the sources back."""

    @override
    def patterns(self) -> "list[URLPattern]":
        """Return the routes of `next.seo.urls` whose source a page tree declares."""
        return served_patterns(PATTERNS)


__all__ = ["SeoRoutesImpl"]
