from pathlib import Path

import pytest
from django.core.checks import Tags
from django.core.checks.registry import registry as check_registry

from next.checks import NEXT, SEO, register_all, reset_check_caches
from next.seo.checks import (
    check_robots_disallow,
    check_robots_file,
    check_robots_single_source,
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_route_collisions,
    check_seo_routes_at_host_root,
    check_seo_sources_below_root,
    check_sitemap_dynamic_routes,
    check_sitemap_items_files,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_labels,
    check_sitemap_templates,
)
from next.seo.checks.roots import loaded_seo_roots
from next.seo.manager import seo_manager
from next.seo.registry import sitemap_items_registry
from tests.seo.trees import routed, write_tree


CHECKS = [
    check_robots_disallow,
    check_robots_file,
    check_robots_single_source,
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_route_collisions,
    check_seo_routes_at_host_root,
    check_seo_sources_below_root,
    check_sitemap_dynamic_routes,
    check_sitemap_items_files,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_labels,
    check_sitemap_templates,
]


class TestRegistration:
    """Every seo check registers under the seo tag and stays silent on a bare tree."""

    @pytest.mark.parametrize("check", CHECKS, ids=[check.__name__ for check in CHECKS])
    def test_every_check_runs_by_default_under_the_seo_tag(self, check) -> None:
        register_all()
        assert set(check.tags) == {Tags.urls, NEXT, SEO}
        assert check in check_registry.registered_checks

    def test_a_tree_without_sources_is_silent(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", pages=("", "posts/[slug]"))):
            for check in CHECKS:
                assert check() == []

    def test_loaded_seo_roots_reads_every_tree_once(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root, root):
            init_errors, roots = loaded_seo_roots()
        assert init_errors == []
        assert [entry.path for entry in roots] == [root]
        assert set(roots[0].trails) == {"", "about"}
        assert roots[0].section == "pages"
        assert roots[0].robots is None
        assert roots[0].robots_file is None


class TestResetCheckCaches:
    """A check reset drops the seo manager memo and the items registry."""

    def test_the_seo_manager_and_the_items_registry_reset(self, tmp_path) -> None:
        def posts() -> list[dict[str, str]]:
            return []

        sitemap_items_registry.register(
            Path(tmp_path) / "sitemap.py", "posts/[slug]", posts
        )
        version = seo_manager.version
        reset_check_caches()
        assert seo_manager.version != version
        assert sitemap_items_registry.entries_for(Path(tmp_path)) == ()
