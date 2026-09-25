import logging

import pytest
from django.test import override_settings

from next.discovery import get_router_manager
from next.seo import seo_manager
from next.seo.discovery import (
    SOURCE_NAMES,
    SeoRoot,
    discover_seo_roots,
    load_seo_source,
    section_label,
    unique_sections,
)
from next.seo.registry import sitemap_items_registry
from tests.seo.trees import listed_elsewhere, routed, write_tree
from tests.support import importable_dir, write_page


def _roots() -> tuple[SeoRoot, ...]:
    manager, _errors = get_router_manager()
    assert manager is not None
    return discover_seo_roots(manager)


class TestSectionLabel:
    """A tree takes the label of its app, else its slugified name, made unique."""

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

    def test_a_repeated_label_takes_a_numeric_suffix(self) -> None:
        assert unique_sections(["pages"] * 3) == ["pages", "pages-2", "pages-3"]

    def test_a_suffix_never_takes_the_label_of_another_tree(self) -> None:
        assert unique_sections(["blog", "blog", "blog-2"]) == [
            "blog",
            "blog-3",
            "blog-2",
        ]
        assert unique_sections(["blog-2", "blog", "blog"]) == [
            "blog-2",
            "blog",
            "blog-3",
        ]


class TestDiscoverSeoRoots:
    """Every routed tree is probed once for its sitemap and robots sources."""

    def test_probes_the_three_sources_of_every_tree(self, tmp_path) -> None:
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
        assert roots[1].sitemap is None
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

    def test_every_tree_keeps_its_own_section_in_the_sitemaps(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "blog", pages=("a1",), sitemap="")
        second = write_tree(tmp_path / "b" / "blog", pages=("b1",), sitemap="")
        third = write_tree(tmp_path / "c" / "blog-2", pages=("c1",), sitemap="")
        with routed(first, second, third):
            assert [root.section for root in _roots()] == ["blog", "blog-3", "blog-2"]
            sitemaps = seo_manager.sitemaps()
        assert {section: sm.root for section, sm in sitemaps.items()} == {
            "blog": first,
            "blog-3": second,
            "blog-2": third,
        }

    def test_a_broken_module_keeps_its_error_with_a_warning(
        self, tmp_path, caplog
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="raise RuntimeError('boom')\n")
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            roots = _roots()
        assert roots[0].sitemap_module is None
        assert roots[0].sitemap is not None
        assert roots[0].sitemap.error is not None
        assert f"{root / 'sitemap.py'} failed to import" in caplog.text

    def test_the_trails_are_walked_once_per_discovery(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"))
        with routed(root):
            [seo_root] = _roots()
            trails = seo_root.trails
            write_page(root, "late")
            assert seo_root.trails is trails
            assert list(trails) == ["", "about"]
            [fresh] = _roots()
            assert sorted(fresh.trails) == ["", "about", "late"]
        assert fresh == seo_root

    def test_the_items_entries_are_those_of_the_root_sitemap(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap="")
        sitemap_items_registry.register(
            root / "sitemap.py", "posts/[slug]", listed_elsewhere
        )
        sitemap_items_registry.register(root / "page.py", "", listed_elsewhere)
        with routed(root):
            [seo_root] = _roots()
        assert seo_root.sitemap_path == root / "sitemap.py"
        assert seo_root.items_entries() == (("posts/[slug]", listed_elsewhere),)

    def test_the_source_names_are_the_three_files(self) -> None:
        assert SOURCE_NAMES == ("sitemap.py", "robots.py", "robots.txt")

    def test_the_walk_skip_names_travel_with_the_root(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root):
            roots = _roots()
        assert "_components" in roots[0].skip_names


class TestLoadSeoSource:
    """A source file loads to its module, and an absent one to nothing."""

    def test_an_absent_file_answers_none(self, tmp_path) -> None:
        assert load_seo_source(tmp_path / "missing.py") is None

    def test_a_module_that_imports_answers_itself(self, tmp_path) -> None:
        (tmp_path / "robots.py").write_text("host = 'acme.example'\n")
        source = load_seo_source(tmp_path / "robots.py")
        assert source is not None
        assert source.error is None
        assert source.module is not None
        assert source.module.host == "acme.example"
