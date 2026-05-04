from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps as django_apps
from django.db.utils import DatabaseError
from django.urls import URLPattern, path

from next.conf import next_framework_settings
from next.urls import FileRouterBackend


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.urls import URLResolver


CATCHALL_URL_PATH = "wiki/[slug]"
PUBLIC_PREFIX = "wiki"


class HybridRouterBackend(FileRouterBackend):
    """File router that also publishes one named URL per Article row.

    The file route at ``wiki/routes/wiki/[slug]/page.py`` handles the
    actual rendering through dependency injection. This backend appends
    a named URL pattern for each existing article slug. The aliases
    share the catchall view but bind a fixed ``slug`` kwarg, so the
    ``DArticle`` provider sees the right URL parameter when called via
    a reversed name. Each alias has a unique reverse name of
    ``wiki_article_<slug>``.
    """

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Return file routes plus a named alias per article."""
        urls = list(super().generate_urls())
        catchall = self._find_catchall(urls)
        if catchall is None:
            return urls
        urls.extend(self._build_article_aliases(catchall.callback))
        return urls

    def _find_catchall(self, urls: list[URLPattern | URLResolver]) -> URLPattern | None:
        """Locate the file pattern that handles every article slug."""
        target = next_framework_settings.URL_NAME_TEMPLATE.format(name="wiki_slug")
        for url in urls:
            if isinstance(url, URLPattern) and getattr(url, "name", None) == target:
                return url
        return None

    def _build_article_aliases(self, view: Callable[..., object]) -> list[URLPattern]:
        """Materialise one named URL per existing article slug."""
        article_model = django_apps.get_model("wiki", "Article")
        try:
            slugs = list(article_model.objects.values_list("slug", flat=True))
        except DatabaseError:  # pragma: no cover - DB not ready before migrations
            return []
        return [
            path(
                f"{PUBLIC_PREFIX}/{slug}/",
                view,
                kwargs={"slug": slug},
                name=f"wiki_article_{slug}",
            )
            for slug in slugs
        ]
