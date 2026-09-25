import logging

from django.test import override_settings

from next.discovery import get_router_manager
from next.seo.discovery import (
    SeoRoot,
    _unique_section,
    discover_seo_roots,
    load_seo_module,
    section_label,
)
from tests.seo.trees import routed, write_tree
from tests.support import importable_dir


def _roots() -> tuple[SeoRoot, ...]:
    manager, _errors = get_router_manager()
    assert manager is not None
    return discover_seo_roots(manager)


class TestSectionLabel:
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

    def test_a_repeated_label_takes_a_numeric_suffix(self) -> None:
        taken: dict[str, int] = {}
        assert [_unique_section("pages", taken) for _ in range(3)] == [
            "pages",
            "pages-2",
            "pages-3",
        ]


class TestDiscoverSeoRoots:
    def test_probes_the_three_sources_of_every_tree(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", sitemap="", robots_txt=b"User-agent: *\n")
        second = write_tree(tmp_path / "b", robots="")
        with routed(first, second):
            roots = _roots()
        assert [root.section for root in roots] == ["a", "b"]
        assert roots[0].sitemap_module == first / "sitemap.py"
        assert roots[0].robots_module is None
        assert roots[0].robots_file == first / "robots.txt"
        assert roots[0].path == first
        assert roots[1].sitemap_module is None
        assert roots[1].robots_module == second / "robots.py"
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

    def test_a_broken_module_is_skipped_with_a_warning(self, tmp_path, caplog) -> None:
        root = write_tree(tmp_path / "pages", sitemap="raise RuntimeError('boom')\n")
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            roots = _roots()
        assert roots[0].sitemap_module is None
        assert f"{root / 'sitemap.py'} failed to import" in caplog.text

    def test_the_walk_skip_names_travel_with_the_root(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root):
            roots = _roots()
        assert "_components" in roots[0].skip_names


class TestLoadSeoModule:
    def test_an_absent_file_answers_none(self, tmp_path) -> None:
        assert load_seo_module(tmp_path / "missing.py") is None

    def test_a_module_that_imports_answers_itself(self, tmp_path) -> None:
        (tmp_path / "robots.py").write_text("host = 'acme.example'\n")
        module = load_seo_module(tmp_path / "robots.py")
        assert module is not None
        assert module.host == "acme.example"
