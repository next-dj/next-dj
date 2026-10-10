from pathlib import Path

import pytest
from django.core.checks import Tags
from django.core.checks.registry import registry as check_registry

import next.checks as facade
from next.checks import NEXT, SEO, register_all, reset_check_caches
from next.seo import checks
from next.seo.manager import seo_manager
from next.seo.registry import SitemapItemsEntry, sitemap_items_registry
from tests.support import routed, write_tree


TAGS = {
    "check_seo_settings": {NEXT, SEO},
    "check_seo_sources_on_closed_site": {NEXT, SEO},
    "check_sitemap_templates": {Tags.templates, NEXT, SEO},
}
CHECKS = [getattr(checks, name) for name in checks.__all__]


class TestRegistration:
    """Every seo check registers under the seo tag and stays silent on a bare tree."""

    @pytest.mark.parametrize("check", CHECKS, ids=[check.__name__ for check in CHECKS])
    def test_every_check_registers_under_the_seo_tag(self, check) -> None:
        register_all()
        expected = TAGS.get(check.__name__, {Tags.urls, NEXT, SEO})
        assert set(check.tags) == expected
        assert check in (
            check_registry.registered_checks | check_registry.deployment_checks
        )

    def test_every_check_is_reachable_through_next_checks(self) -> None:
        assert set(checks.__all__) <= set(facade.__all__)
        assert all(
            getattr(facade, name) is getattr(checks, name) for name in checks.__all__
        )

    def test_a_tree_without_sources_is_silent(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", pages=("", "posts/[slug]"))):
            for check in CHECKS:
                assert check() == [], check.__name__


class TestResetCheckCaches:
    """A check reset drops the seo manager memo and the items registry."""

    def test_the_seo_manager_and_the_items_registry_reset(self, tmp_path) -> None:
        def posts() -> list[dict[str, str]]:
            return []

        sitemap_items_registry.register(
            SitemapItemsEntry(Path(tmp_path) / "sitemap.py", "posts/[slug]", posts)
        )
        version = seo_manager.version
        reset_check_caches()
        assert seo_manager.version != version
        assert sitemap_items_registry.entries_for(Path(tmp_path) / "sitemap.py") == ()
