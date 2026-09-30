import pytest
from django.contrib.sitemaps import Sitemap

from next.pages.loaders import reset_module_memo
from next.pages.responses import NO_STORE, cache_control
from next.seo import PageTreeSitemapBackend, SitemapBackend
from next.seo.backends import shortest_cache
from tests.support import POSTS_ITEMS, routed, write_page, write_tree


class _Bare(SitemapBackend):
    """A backend overriding nothing but its sections."""

    def sections(self, request):
        """Answer one empty Django sitemap."""
        return {"bare": Sitemap()}


class TestSitemapBackend:
    """The base keeps its entry and serves, uncached, whatever it lists."""

    def test_the_defaults_serve_uncached(self) -> None:
        backend = _Bare({"BACKEND": "x", "OPTIONS": {"a": 1}})
        assert backend.config == {"BACKEND": "x", "OPTIONS": {"a": 1}}
        assert backend.options == {"a": 1}
        assert backend.serves() is True
        assert backend.cache_control() is None

    def test_missing_options_read_as_empty(self) -> None:
        assert _Bare({}).options == {}
        assert _Bare({"OPTIONS": None}).options == {}

    def test_the_base_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            SitemapBackend({})


class TestPageTreeSitemapBackend:
    """The page-tree backend answers one section per tree and per `section=`."""

    def test_it_serves_while_a_tree_carries_a_sitemap_py(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "bare")):
            assert PageTreeSitemapBackend({}).serves() is False
        with routed(write_tree(tmp_path / "broken", sitemap="raise ValueError\n")):
            backend = PageTreeSitemapBackend({})
            assert backend.serves() is True
            assert backend.sections(None) == {}

    def test_the_shortest_cache_wins(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", sitemap="cache = 600\n")
        second = write_tree(tmp_path / "b", sitemap="cache = 60\n")
        third = write_tree(tmp_path / "c", sitemap="")
        with routed(first, second, third):
            assert PageTreeSitemapBackend({}).cache_control() == cache_control(60)
        with routed(third):
            assert PageTreeSitemapBackend({}).cache_control() is None

    def test_a_cache_dict_reads_like_a_page_cache(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", sitemap="cache = {'public': True, 's_maxage': 300}\n"
        )
        with routed(root):
            control = PageTreeSitemapBackend({}).cache_control()
        assert control == cache_control({"public": True, "s_maxage": 300})

    def test_no_store_ranks_ahead_of_every_age(self) -> None:
        assert shortest_cache([cache_control(60), None, NO_STORE]) is NO_STORE
        assert shortest_cache([None]) is None

    @pytest.mark.parametrize(
        ("controls", "winner"),
        [
            ([cache_control({"public": True}), NO_STORE], NO_STORE),
            ([cache_control(0), NO_STORE], NO_STORE),
            ([cache_control({"vary": ["Accept"]}), cache_control(3600)], 1),
        ],
        ids=["public-vs-no-store", "zero-age-first", "vary-only-vs-an-age"],
    )
    def test_a_cache_naming_no_age_never_outranks_one_that_does(
        self, controls, winner
    ) -> None:
        expected = controls[winner] if isinstance(winner, int) else winner
        assert shortest_cache(controls) is expected

    def test_a_named_section_takes_its_items_out_of_the_tree(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "posts/[slug]", "tags/[tag]"),
            sitemap=POSTS_ITEMS + "\n\n@sitemap.items('tags/[tag]', section='tags')\n"
            "def tags():\n    return [{'tag': 'x'}]\n",
        )
        with routed(root):
            sections = PageTreeSitemapBackend({}).sections(None)
            assert list(sections) == ["pages", "tags"]
            assert [item.trail for item in sections["pages"].items()] == [
                "",
                "posts/[slug]",
            ]
            assert [item.trail for item in sections["tags"].items()] == ["tags/[tag]"]

    def test_an_excluded_items_trail_drops_the_whole_part(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "posts/[slug]"),
            sitemap="exclude = ['posts/*']\n" + POSTS_ITEMS,
        )
        with routed(root):
            [section] = PageTreeSitemapBackend({}).sections(None).values()
            assert [item.trail for item in section.items()] == [""]

    def test_the_first_tree_keeps_a_section_two_name(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", pages=("one",), sitemap="section = 'x'\n")
        second = write_tree(
            tmp_path / "b",
            pages=("two/[slug]",),
            sitemap="from next.seo import sitemap\n\n"
            "@sitemap.items('two/[slug]', section='x')\n"
            "def two():\n    return [{'slug': 'a'}]\n",
        )
        with routed(first, second):
            sections = PageTreeSitemapBackend({}).sections(None)
            assert [item.trail for item in sections["x"].items()] == ["one"]

    def test_the_static_routes_wait_for_a_module_reload(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root):
            backend = PageTreeSitemapBackend({})
            [seo_root] = backend.roots()
            first = backend.sections(None)["pages"]
            options = first.options
            listed = backend.static_trails(seo_root, options)
            assert backend.static_trails(seo_root, options) is listed
            write_page(root, "late")
            reset_module_memo()
            assert backend.static_trails(seo_root, options) is not listed
