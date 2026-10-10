"""SEO routes implementation bound into the `next.ports` slot at app startup."""

from typing import TYPE_CHECKING, override

from next.ports import SeoRoutes

from .routes import served_patterns
from .urls import PATTERNS


if TYPE_CHECKING:
    from django.urls import URLPattern


class SeoRoutesImpl(SeoRoutes):
    """Provide the SEO URL patterns whose source exists."""

    @override
    def patterns(self) -> "list[URLPattern]":
        """Return the patterns of `next.seo.urls` whose source exists."""
        return served_patterns(PATTERNS)


__all__ = ["SeoRoutesImpl"]
