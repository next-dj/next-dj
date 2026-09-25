from django.conf import settings
from django.core.checks import WARNING
from django.test import override_settings

from next.seo.checks import (
    check_sitemap_dynamic_routes,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_labels,
    check_sitemap_templates,
)
from tests.seo.trees import NOINDEX, POSTS_ITEMS, routed, write_tree
from tests.support import check_ids, write_page


NO_APP_DIRS = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {},
    }
]


UNKNOWN_ITEMS = """
from next.seo import sitemap


@sitemap.items("nope/[slug]")
def nope():
    yield {"slug": "a"}
"""


class TestItemsTrails:
    """`@sitemap.items` names a trail the tree routes (`next.E111`)."""

    def test_an_unrouted_trail_is_an_error(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=UNKNOWN_ITEMS
        )
        with routed(root):
            messages = check_sitemap_items_trails()
        assert check_ids(messages) == ["next.E111"]
        assert "@sitemap.items('nope/[slug]')" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")

    def test_a_routed_trail_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_items_trails() == []


class TestSitemapTemplates:
    """A served sitemap needs the Django sitemap templates (`next.E112`)."""

    def test_missing_templates_are_an_error_while_a_sitemap_exists(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root), override_settings(TEMPLATES=NO_APP_DIRS):
            messages = check_sitemap_templates()
        assert check_ids(messages) == ["next.E112"]
        assert "sitemap.xml, sitemap_index.xml" in messages[0].msg
        assert "django.contrib.sitemaps" in messages[0].msg
        assert messages[0].obj is settings

    def test_the_templates_load_by_default(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", sitemap="")):
            assert check_sitemap_templates() == []

    def test_no_sitemap_asks_for_no_template(self, tmp_path) -> None:
        with (
            routed(write_tree(tmp_path / "pages")),
            override_settings(TEMPLATES=NO_APP_DIRS),
        ):
            assert check_sitemap_templates() == []


class TestSectionLabels:
    """Trees sharing a section label serve numbered sections (`next.W104`)."""

    def test_two_trees_with_one_label_warn(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages", sitemap="")
        second = write_tree(tmp_path / "b" / "pages")
        with routed(first, second):
            messages = check_sitemap_section_labels()
        assert check_ids(messages) == ["next.W104"]
        assert messages[0].level == WARNING
        assert f"{first}, {second} share the sitemap section label 'pages'" in (
            messages[0].msg
        )
        assert "serve as the numbered sections pages, pages-2" in messages[0].msg
        assert messages[0].obj == str(second)

    def test_the_warning_names_the_sections_actually_served(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "blog", sitemap="")
        second = write_tree(tmp_path / "b" / "blog")
        third = write_tree(tmp_path / "c" / "blog-2")
        with routed(first, second, third):
            [message] = check_sitemap_section_labels()
        assert "serve as the numbered sections blog, blog-3" in message.msg

    def test_distinct_labels_pass(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", sitemap="")
        second = write_tree(tmp_path / "b", sitemap="")
        with routed(first, second):
            assert check_sitemap_section_labels() == []

    def test_labels_are_free_without_a_sitemap(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages")
        second = write_tree(tmp_path / "b" / "pages")
        with routed(first, second):
            assert check_sitemap_section_labels() == []


class TestDynamicRoutes:
    """A dynamic route the sitemap neither lists nor excludes warns (`next.W097`)."""

    def test_an_unlisted_dynamic_route_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap="")
        with routed(root):
            messages = check_sitemap_dynamic_routes()
        assert check_ids(messages) == ["next.W097"]
        assert "@sitemap.items('posts/[slug]')" in messages[0].msg
        assert messages[0].obj == str(root / "posts" / "[slug]" / "page.py")

    def test_a_listed_route_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_dynamic_routes() == []

    def test_an_excluded_route_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap='exclude = ["posts/*"]'
        )
        with routed(root):
            assert check_sitemap_dynamic_routes() == []

    def test_the_trail_the_warning_names_excludes_it(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap='exclude = ["posts/[slug]"]',
        )
        with routed(root):
            assert check_sitemap_dynamic_routes() == []


class TestNoindexItems:
    """Items listed for a noindex page warn (`next.W098`)."""

    def test_items_on_a_noindex_page_warn(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=POSTS_ITEMS)
        page_path = write_page(root, "posts/[slug]", NOINDEX)
        with routed(root):
            messages = check_sitemap_noindex_items()
        assert check_ids(messages) == ["next.W098"]
        assert "is noindex by its static metadata" in messages[0].msg
        assert messages[0].obj == str(page_path)

    def test_items_on_an_indexed_page_pass(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_noindex_items() == []
