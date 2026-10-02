import pytest
from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.core.checks import WARNING
from django.test import override_settings

from next.seo import SitemapBackend
from next.seo.checks import (
    check_sitemap_dynamic_routes,
    check_sitemap_excluded_items,
    check_sitemap_i18n_options,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_collisions,
    check_sitemap_section_labels,
    check_sitemap_templates,
)
from tests.support import (
    CLOSED_SITE,
    I18N,
    I18N_URLCONF,
    NOINDEX,
    POSTS_ITEMS,
    check_ids,
    routed,
    write_page,
    write_tree,
)


def refusing_rule(request: object) -> bool:
    """Fail the test the moment a check calls the rule."""
    raise AssertionError


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
    """Trees sharing a section label serve numbered sections (`next.W099`)."""

    def test_two_trees_with_one_label_warn(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages", sitemap="")
        second = write_tree(tmp_path / "b" / "pages")
        with routed(first, second):
            messages = check_sitemap_section_labels()
        assert check_ids(messages) == ["next.W099"]
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
    """A dynamic route the sitemap neither lists nor excludes warns (`next.W092`)."""

    def test_an_unlisted_dynamic_route_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap="")
        with routed(root):
            messages = check_sitemap_dynamic_routes()
        assert check_ids(messages) == ["next.W092"]
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

    def test_a_statically_noindex_route_passes(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        write_page(root, "posts/[slug]", NOINDEX)
        with routed(root):
            assert check_sitemap_dynamic_routes() == []


class TestNoindexItems:
    """Items listed for a noindex page warn (`next.W093`)."""

    def test_items_on_a_noindex_page_warn(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=POSTS_ITEMS)
        page_path = write_page(root, "posts/[slug]", NOINDEX)
        with routed(root):
            messages = check_sitemap_noindex_items()
        assert check_ids(messages) == ["next.W093"]
        assert "is noindex by its static metadata" in messages[0].msg
        assert messages[0].obj == str(page_path)

    def test_items_on_an_indexed_page_pass(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_noindex_items() == []

    def test_a_closed_site_is_silent(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=POSTS_ITEMS)
        write_page(root, "posts/[slug]", NOINDEX)
        with routed(root, **CLOSED_SITE):
            assert check_sitemap_noindex_items() == []

    def test_a_callable_rule_is_never_called_and_reads_open(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=POSTS_ITEMS)
        write_page(root, "posts/[slug]", NOINDEX)
        with routed(root, SITE={"INDEXABLE": refusing_rule}):
            messages = check_sitemap_noindex_items()
        assert check_ids(messages) == ["next.W093"]


class TestI18nOptions:
    """i18n options that take no effect warn (`next.W100`, `next.W101`)."""

    @override_settings(**I18N)
    def test_every_inconsistent_option_is_named(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", sitemap="x_default = True\nlanguages = ['en', 'fr']\n"
        )
        with routed(root, urlconf=I18N_URLCONF):
            messages = check_sitemap_i18n_options()
        assert check_ids(messages) == ["next.W100"] * 3
        assert "take effect only with i18n = True" in messages[0].msg
        assert "Set i18n = True, or drop alternates and x_default." in messages[0].msg
        assert "only with alternates = True" in messages[1].msg
        assert "Set alternates = True, or drop x_default." in messages[1].msg
        assert "languages names 'fr'" in messages[2].msg
        assert "Add the codes to settings.LANGUAGES" in messages[2].msg

    def test_i18n_without_language_prefixes_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="i18n = True\n")
        with routed(root):
            messages = check_sitemap_i18n_options()
        assert check_ids(messages) == ["next.W101"]
        assert messages[0].obj == str(root / "sitemap.py")

    @override_settings(**I18N)
    def test_consistent_options_under_prefixes_pass(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            sitemap="i18n = True\nalternates = True\nx_default = True\n",
        )
        with routed(root, urlconf=I18N_URLCONF):
            assert check_sitemap_i18n_options() == []


class TestLimitUnderAlternates:
    """A `limit` a page of alternates would outgrow warns (`next.W086`)."""

    @pytest.mark.parametrize(
        ("options", "effective"),
        [("", 16666), ("x_default = True\n", 12500)],
        ids=["alternates", "x_default"],
    )
    @override_settings(**I18N)
    def test_a_limit_above_the_effective_one_warns(
        self, tmp_path, options, effective
    ) -> None:
        sitemap = "i18n = True\nalternates = True\nlimit = 50000\n" + options
        root = write_tree(tmp_path / "pages", sitemap=sitemap)
        with routed(root, urlconf=I18N_URLCONF):
            messages = check_sitemap_i18n_options()
        assert check_ids(messages) == ["next.W086"]
        assert f"pages hold {effective} URLs instead" in messages[0].msg
        assert f"Lower limit to {effective} or less" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")

    @pytest.mark.parametrize(
        "sitemap",
        [
            "i18n = True\nalternates = True\nlimit = 100\n",
            "i18n = True\nalternates = True\n",
            "i18n = True\nlimit = 50000\n",
            "i18n = True\nalternates = True\nlimit = 'many'\n",
        ],
        ids=["within", "no_limit", "no_alternates", "not_an_int"],
    )
    @override_settings(**I18N)
    def test_a_limit_that_fits_passes(self, tmp_path, sitemap) -> None:
        root = write_tree(tmp_path / "pages", sitemap=sitemap)
        with routed(root, urlconf=I18N_URLCONF):
            assert check_sitemap_i18n_options() == []


class TestExcludedItems:
    """`@sitemap.items` on a trail `exclude` covers lists nothing (`next.W102`)."""

    def test_an_excluded_items_trail_warns(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap="exclude = ['posts/*']\n" + POSTS_ITEMS,
        )
        with routed(root):
            messages = check_sitemap_excluded_items()
        assert check_ids(messages) == ["next.W102"]
        assert "@sitemap.items('posts/[slug]')" in messages[0].msg

    def test_an_items_trail_outside_exclude_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap="exclude = ['admin/*']\n" + POSTS_ITEMS,
        )
        with routed(root):
            assert check_sitemap_excluded_items() == []


class ExtraBackend(SitemapBackend):
    """A backend serving a section named `pages`, and one failing to list."""

    def sections(self, request):
        """Answer the section the page tree names too."""
        return {"pages": Sitemap()}


class FailingBackend(SitemapBackend):
    """A backend whose sections raise."""

    def sections(self, request):
        """Raise, as a backend reading a missing table would."""
        raise RuntimeError


class TestSectionCollisions:
    """Two sources naming one section is an error (`next.E116`)."""

    def test_an_items_section_taken_by_another_tree_is_an_error(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", pages=("one",), sitemap="section = 'x'\n")
        second = write_tree(
            tmp_path / "b",
            pages=("two/[slug]",),
            sitemap="from next.seo import sitemap\n\n"
            "@sitemap.items('two/[slug]', section='x')\n"
            "def two():\n    return []\n",
        )
        with routed(first, second):
            messages = check_sitemap_section_collisions()
        assert check_ids(messages) == ["next.E116"]
        assert f"@sitemap.items(section='x') in {second / 'sitemap.py'}" in (
            messages[0].msg
        )
        assert f"which {first / 'sitemap.py'} already serves" in messages[0].msg

    def test_a_backend_section_taken_by_a_tree_is_an_error(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        backends = [
            {"BACKEND": "next.seo.PageTreeSitemapBackend"},
            {"BACKEND": "tests.seo.checks.test_sitemaps.FailingBackend"},
            {"BACKEND": "tests.seo.checks.test_sitemaps.ExtraBackend"},
        ]
        with routed(root, SEO={"SITEMAP_BACKENDS": backends}):
            messages = check_sitemap_section_collisions()
        assert check_ids(messages) == ["next.W089", "next.E116"]
        failing, taken = messages
        assert failing.obj == "tests.seo.checks.test_sitemaps.FailingBackend"
        assert failing.msg.startswith(
            "tests.seo.checks.test_sitemaps.FailingBackend.sections(None) raised "
        )
        assert "Make sections() work without a request" in failing.msg
        assert taken.obj == "tests.seo.checks.test_sitemaps.ExtraBackend"

    def test_distinct_sections_pass(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap="from next.seo import sitemap\n\n"
            "@sitemap.items('posts/[slug]', section='posts')\n"
            "def posts():\n    return []\n",
        )
        with routed(root):
            assert check_sitemap_section_collisions() == []
