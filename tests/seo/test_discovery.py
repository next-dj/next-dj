import logging
from types import ModuleType

import pytest
from django.test import override_settings

from next.discovery import get_router_manager
from next.pages.loaders import has_load_errors
from next.seo import SeoSourceImportError, seo_manager
from next.seo.discovery import (
    SOURCE_NAMES,
    SeoRoot,
    declared_section,
    discover_seo_roots,
    forget_page_tree_roots,
    load_source,
    page_tree_roots,
    section_label,
    source_stamps,
)
from next.seo.registry import sitemap_items_registry
from tests.support import POSTS_ITEMS, importable_dir, routed, write_page, write_tree


def _roots() -> tuple[SeoRoot, ...]:
    manager, _errors = get_router_manager()
    assert manager is not None
    return discover_seo_roots(manager)


class TestSectionLabel:
    """A tree takes the section its sitemap declares, else the label of its app."""

    def test_a_tree_under_an_installed_app_takes_the_app_label(self, tmp_path) -> None:
        app = tmp_path / "seo_app"
        (app / "pages").mkdir(parents=True)
        (app / "__init__.py").write_text("")
        with (
            importable_dir(tmp_path),
            override_settings(INSTALLED_APPS=["django.contrib.staticfiles", "seo_app"]),
        ):
            assert section_label(app / "pages") == "seo_app"

    def test_a_tree_outside_every_app_takes_its_slugified_name(self, tmp_path) -> None:
        assert section_label(tmp_path / "My Pages") == "my-pages"

    def test_a_name_that_slugifies_to_nothing_reads_as_root(self, tmp_path) -> None:
        assert section_label(tmp_path / "!!!") == "root"

    @pytest.mark.parametrize(
        "apps_order", [["outer", "outer.inner"], ["outer.inner", "outer"]]
    )
    def test_the_innermost_app_labels_a_nested_tree(self, tmp_path, apps_order) -> None:
        inner = tmp_path / "outer" / "inner"
        (inner / "pages").mkdir(parents=True)
        (tmp_path / "outer" / "__init__.py").write_text("")
        (inner / "__init__.py").write_text("")
        with (
            importable_dir(tmp_path),
            override_settings(
                INSTALLED_APPS=["django.contrib.staticfiles", *apps_order]
            ),
        ):
            assert section_label(inner / "pages") == "inner"
            assert section_label(tmp_path / "outer" / "pages") == "outer"

    def test_the_section_a_sitemap_declares_wins(self, tmp_path) -> None:
        module = ModuleType("sitemap")
        module.section = "blog"
        assert section_label(tmp_path / "pages", module) == "blog"

    @pytest.mark.parametrize("value", ["not a slug", "", 3, None])
    def test_a_section_that_is_no_slug_is_ignored(self, tmp_path, value) -> None:
        module = ModuleType("sitemap")
        module.section = value
        assert declared_section(module) is None
        assert section_label(tmp_path / "pages", module) == "pages"


class TestDiscoverSeoRoots:
    """Every routed tree is probed once for its three SEO sources."""

    def test_probes_every_source_of_every_tree(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", sitemap="", robots_txt=b"User-agent: *\n")
        second = write_tree(tmp_path / "b", robots="")
        with routed(first, second):
            roots = _roots()
        assert [root.section for root in roots] == ["a", "b"]
        assert roots[0].sitemap is not None
        assert roots[0].sitemap.path == first / "sitemap.py"
        assert roots[0].sitemap_module is roots[0].sitemap.module
        assert roots[0].robots is None
        assert roots[0].robots_module is None
        assert roots[0].robots_file == first / "robots.txt"
        assert roots[0].path == first
        assert roots[1].sitemap_module is None
        assert roots[1].robots is not None
        assert roots[1].robots.path == second / "robots.py"
        assert roots[1].robots_file is None

    def test_a_tree_reported_twice_is_probed_once(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root, root):
            roots = _roots()
        assert [r.path for r in roots] == [root]

    def test_two_trees_named_alike_get_distinct_sections(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages")
        second = write_tree(tmp_path / "b" / "pages")
        with routed(first, second):
            roots = _roots()
        assert [root.section for root in roots] == ["pages", "pages-2"]
        assert [root.label for root in roots] == ["pages", "pages"]

    def test_a_declared_section_names_the_tree(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages", sitemap="section = 'blog'\n")
        second = write_tree(tmp_path / "b" / "pages", sitemap="")
        with routed(first, second):
            roots = _roots()
        assert [root.section for root in roots] == ["blog", "pages"]

    def test_the_trails_are_walked_at_discovery(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"))
        with routed(root):
            [seo_root] = _roots()
            write_page(root, "late")
            assert list(seo_root.trails) == ["", "about"]
            [fresh] = _roots()
        assert sorted(fresh.trails) == ["", "about", "late"]

    def test_the_items_entries_are_those_of_the_root_sitemap(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            [seo_root] = _roots()
        assert seo_root.sitemap_path == root / "sitemap.py"
        assert [entry.trail for entry in seo_root.items_entries()] == ["posts/[slug]"]

    def test_the_walk_skip_names_travel_with_the_root(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root):
            roots = _roots()
        assert "_components" in roots[0].skip_names


class TestLoadSource:
    """A source loads through its own loader, a failure kept on the source."""

    def test_an_absent_file_answers_none(self, tmp_path) -> None:
        assert load_source(tmp_path / "missing.py") is None

    def test_a_module_that_imports_answers_itself(self, tmp_path) -> None:
        (tmp_path / "robots.py").write_text("cache = 60\n")
        source = load_source(tmp_path / "robots.py")
        assert source is not None
        assert source.error is None
        assert source.module is not None
        assert source.module.cache == 60

    def test_a_broken_module_keeps_its_cause_and_logs_it_once(
        self, tmp_path, caplog
    ) -> None:
        path = tmp_path / "robots.py"
        path.write_text("raise RuntimeError('boom')\n")
        with caplog.at_level(logging.ERROR, logger="next.seo"):
            source = load_source(path)
        assert source is not None
        assert source.module is None
        assert isinstance(source.error, SeoSourceImportError)
        assert source.error.path == path
        assert isinstance(source.error.__cause__, RuntimeError)
        assert caplog.text.count(f"{path} failed to import") == 1

    def test_a_broken_source_leaves_the_page_health_flag_down(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="raise RuntimeError('boom')\n")
        with routed(root):
            [seo_root] = _roots()
        assert seo_root.robots is not None
        assert seo_root.robots.error is not None
        assert has_load_errors() is False

    def test_executing_a_sitemap_again_starts_its_registrations_afresh(
        self, tmp_path
    ) -> None:
        path = tmp_path / "sitemap.py"
        path.write_text(POSTS_ITEMS)
        load_source(path)
        path.write_text("")
        load_source(path)
        assert sitemap_items_registry.entries_for(path) == ()


class TestSourceStamps:
    """A discovered tree tells whether a source file moved since it was read."""

    def test_the_stamps_follow_the_source_names(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="")
        stamps = source_stamps(root)
        assert len(stamps) == len(SOURCE_NAMES)
        assert stamps[SOURCE_NAMES.index("robots.py")] is not None
        assert stamps[SOURCE_NAMES.index("sitemap.py")] is None

    def test_a_new_file_makes_the_tree_stale(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root):
            [seo_root] = _roots()
        assert not seo_root.stale()
        (root / "robots.txt").write_bytes(b"User-agent: *\n")
        assert seo_root.stale()


class TestPageTreeRoots:
    """The routed trees are discovered once until the manager forgets them."""

    def test_the_trees_are_memoised_until_forgotten(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            first = page_tree_roots()
            assert page_tree_roots() is first
            forget_page_tree_roots()
            assert page_tree_roots() is not first

    def test_a_manager_reset_forgets_the_trees(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            first = page_tree_roots()
            seo_manager.reset()
            assert page_tree_roots() is not first
