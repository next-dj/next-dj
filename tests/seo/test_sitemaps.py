import logging
import os
import types
from datetime import UTC, date, datetime

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import translation

from next.pages.metadata.hreflang import hreflang_urls, x_default_url
from next.pages.responses import NO_STORE, cache_control
from next.seo import SitemapEntry, SitemapTrailError
from next.seo.backends import PageTreeSitemapBackend
from next.seo.origin import OriginSite
from next.seo.sitemaps import (
    MAX_LIMIT,
    PageTreeSitemap,
    SitemapItem,
    SitemapOptions,
    _mtime,
    is_excluded,
    lastmod_datetime,
    listed_trails,
    static_noindex,
)
from tests.support import (
    BASE,
    I18N,
    I18N_URLCONF,
    NOINDEX,
    WITH_BASE,
    routed,
    write_page,
    write_tree,
)


SITE = OriginSite("acme.example")


def _section(name: str | None = None, request=None) -> PageTreeSitemap:
    sections = PageTreeSitemapBackend({}).sections(request)
    section = sections[name] if name is not None else next(iter(sections.values()))
    assert isinstance(section, PageTreeSitemap)
    return section


def _locations(section: PageTreeSitemap, page: int = 1) -> list[str]:
    return [url["location"] for url in section.get_urls(page, SITE, "https")]


def _items(source: str) -> str:
    return (
        "from next.seo import SitemapEntry, sitemap\n"
        "from datetime import date\n"
        "from django.contrib.auth.models import User\n\n" + source
    )


class TestSitemapOptions:
    """One lenient reader serves the sitemap attributes and the cache lifetime."""

    def test_a_module_declaring_nothing_reads_the_defaults(self) -> None:
        assert SitemapOptions.read(types.ModuleType("sitemap")) == SitemapOptions()

    @pytest.mark.parametrize(
        ("name", "value", "read"),
        [
            ("limit", True, MAX_LIMIT),
            ("limit", -3, MAX_LIMIT),
            ("limit", 10, 10),
            ("limit", 60000, MAX_LIMIT),
            ("priority", True, None),
            ("priority", 1, 1.0),
            ("cache", True, None),
            ("cache", 0, cache_control(0)),
            ("cache", 60, cache_control(60)),
            ("cache", False, NO_STORE),
            ("cache", lambda: 60, None),
            ("changefreq", "", None),
            ("protocol", 5, None),
            ("protocol", "http", "http"),
            ("languages", "en", ()),
            ("languages", ["en", 5, "de"], ("en", "de")),
            ("exclude", ["admin/*", 3], ("admin/*",)),
            ("i18n", 1, True),
        ],
    )
    def test_each_attribute_reads_only_its_own_shape(
        self, name: str, value: object, read: object
    ) -> None:
        module = types.ModuleType("sitemap")
        setattr(module, name, value)
        assert getattr(SitemapOptions.read(module), name) == read


class TestExclude:
    """Only `*` and `?` are wildcards, so brackets in a glob stay literal."""

    def test_brackets_in_a_glob_are_literal(self) -> None:
        assert is_excluded("posts/[slug]", ("posts/[slug]",)) is True
        assert is_excluded("posts/s", ("posts/[slug]",)) is False
        assert is_excluded("posts/[slug]/edit", ("posts/*",)) is True
        assert is_excluded("posts/[id]", ("posts/[??]",)) is True
        assert is_excluded("posts/[slug]", ("posts/[??]",)) is False
        assert is_excluded("posts.x", ("posts?x",)) is True
        assert is_excluded("postsx", ("posts.x",)) is False


class TestLastmodDatetime:
    """A `lastmod` reads as an aware datetime in the current time zone."""

    def test_a_date_reads_as_midnight(self) -> None:
        assert lastmod_datetime(date(2026, 1, 2)) == datetime(2026, 1, 2, tzinfo=UTC)

    def test_a_naive_datetime_takes_the_current_zone(self) -> None:
        with override_settings(TIME_ZONE="America/Chicago"):
            naive = datetime(2026, 1, 2, 3, 0, tzinfo=UTC).replace(tzinfo=None)
            value = lastmod_datetime(naive)
        assert value == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)

    def test_an_aware_datetime_stays_as_it_is(self) -> None:
        value = datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
        assert lastmod_datetime(value) is value


class TestListedTrails:
    """A static trail is listed unless an exclude glob or noindex keeps it out."""

    def test_dynamic_excluded_and_noindex_trails_stay_out(self, tmp_path) -> None:
        trails = {
            trail: write_page(tmp_path, trail)
            for trail in ("", "about", "admin/users", "posts/[slug]")
        }
        trails["draft"] = write_page(tmp_path, "draft", NOINDEX)
        assert listed_trails(trails, ("admin/*",)) == ["", "about"]

    def test_a_noindex_page_reads_noindex(self, tmp_path) -> None:
        assert static_noindex(write_page(tmp_path, "", NOINDEX)) is True

    def test_a_conflicting_page_reads_as_indexed(self, tmp_path, caplog) -> None:
        page_path = write_page(
            tmp_path,
            "",
            "from next.pages import page\n"
            'metadata = {"title": "a"}\n'
            "@page.metadata\n"
            "def meta():\n"
            '    return {"title": "b"}\n',
        )
        with caplog.at_level(logging.WARNING, logger="next.seo"):
            assert static_noindex(page_path) is False
            assert static_noindex(page_path) is False
        refused = [r for r in caplog.records if "is refused" in r.getMessage()]
        assert len(refused) == 1

    def test_an_edit_of_a_refused_page_warns_again(self, tmp_path, caplog) -> None:
        page_path = write_page(
            tmp_path,
            "",
            "from next.pages import page\n"
            'metadata = {"title": "a"}\n'
            "@page.metadata\n"
            "def meta():\n"
            '    return {"title": "b"}\n',
        )
        with caplog.at_level(logging.WARNING, logger="next.seo"):
            static_noindex(page_path)
            stat = page_path.stat()
            os.utime(page_path, (stat.st_atime, stat.st_mtime + 5))
            static_noindex(page_path)
        refused = [r for r in caplog.records if "is refused" in r.getMessage()]
        assert len(refused) == 2

    def test_a_vanished_page_has_no_mtime(self, tmp_path) -> None:
        assert _mtime(tmp_path / "gone.py") is None


class TestPageTreeSitemap:
    """A section reads its options, and its routes and items reverse lazily."""

    def test_the_options_become_the_django_attributes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            sitemap="limit = 5\nprotocol = 'http'\ni18n = True\n"
            "languages = ['de']\nalternates = True\nx_default = True\n",
        )
        with routed(root):
            section = _section()
        assert (section.limit, section.protocol, section.i18n) == (5, "http", True)
        assert section.languages == ["de"]
        assert section.alternates is True
        assert section.x_default is False
        assert section.language_codes() == ["de"]

    def test_the_languages_default_to_every_code(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="i18n = True\n")
        with routed(root), override_settings(**I18N):
            assert _section().language_codes() == ["en", "de"]

    def test_the_static_routes_list_first_and_the_parts_build_once(
        self, tmp_path
    ) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "about", "posts/[slug]"),
            sitemap=_items(
                "@sitemap.items('posts/[slug]')\ndef posts():\n    return [{'slug': 'a'}]\n"
            ),
        )
        with routed(root, **WITH_BASE):
            section = _section()
            assert section.entries() is section.entries()
            assert section.items() is section.entries()
            assert _locations(section) == [
                f"{BASE}/",
                f"{BASE}/about/",
                f"{BASE}/posts/a/",
            ]

    def test_an_items_trail_drops_the_route_it_claims(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "about"),
            sitemap=_items(
                "@sitemap.items('about')\ndef about():\n"
                "    return [SitemapEntry(lastmod=date(2026, 1, 2), priority=0.9)]\n"
            ),
        )
        with routed(root, **WITH_BASE):
            items = list(_section().entries())
        assert [item.trail for item in items] == ["", "about"]
        assert items[1] == SitemapItem(
            "about", SitemapEntry(lastmod=date(2026, 1, 2), priority=0.9)
        )

    def test_the_item_hints_win_over_the_module_defaults(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", sitemap="changefreq = 'weekly'\npriority = 0.8\n"
        )
        with routed(root):
            section = _section()
        own = SitemapItem("", SitemapEntry(changefreq="daily", priority=0.3))
        bare = SitemapItem("", SitemapEntry(lastmod=date(2026, 1, 1)))
        assert (section.changefreq(own), section.priority(own)) == ("daily", 0.3)
        assert (section.changefreq(bare), section.priority(bare)) == ("weekly", 0.8)
        assert section.lastmod(own) is None
        assert section.lastmod(bare) == datetime(2026, 1, 1, tzinfo=UTC)

    def test_an_unrouted_trail_raises_at_build(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            sitemap=_items(
                "@sitemap.items('nope/[slug]')\ndef nope():\n    return []\n"
            ),
        )
        with routed(root), pytest.raises(SitemapTrailError) as caught:
            _section().entries()
        assert caught.value.file == root / "sitemap.py"

    def test_a_generator_is_read_to_a_list(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=_items(
                "@sitemap.items('posts/[slug]')\ndef posts():\n"
                "    yield {'slug': 'a'}\n    yield SitemapEntry({'slug': 'b'})\n"
            ),
        )
        with routed(root, **WITH_BASE):
            section = _section()
            assert len(section.entries()) == 2
            assert _locations(section) == [f"{BASE}/posts/a/", f"{BASE}/posts/b/"]

    @pytest.mark.parametrize(
        "answer", ["None", "'slugs'", "{'slug': 'a'}", "42"], ids=str
    )
    def test_an_answer_that_is_no_rows_raises(self, tmp_path, answer: str) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=_items(
                f"@sitemap.items('posts/[slug]')\ndef posts():\n    return {answer}\n"
            ),
        )
        with routed(root), pytest.raises(TypeError, match="instead of a sequence"):
            _section().entries()

    def test_a_row_without_kwargs_raises_naming_the_callable(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=_items(
                "@sitemap.items('posts/[slug]')\ndef posts():\n    return [42]\n"
            ),
        )
        with (
            routed(root, **WITH_BASE),
            pytest.raises(TypeError, match=r'"posts".*listed int, which reverses'),
        ):
            _locations(_section())

    def test_the_kwargs_and_lastmod_options_read_each_row(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=_items(
                "from types import SimpleNamespace\n\n"
                "@sitemap.items('posts/[slug]', kwargs=lambda row: {'slug': "
                "row['slug'] if isinstance(row, dict) else row.slug}, "
                "lastmod='updated')\n"
                "def posts():\n"
                "    return [SimpleNamespace(slug='a', updated=date(2026, 1, 3)),"
                " {'slug': 'b', 'updated': date(2026, 1, 4)},"
                " SimpleNamespace(slug='c', updated='never')]\n"
            ),
        )
        with routed(root, **WITH_BASE):
            items = list(_section().entries())
        assert [item.entry.kwargs["slug"] for item in items] == ["a", "b", "c"]
        assert [item.entry.lastmod for item in items] == [
            date(2026, 1, 3),
            date(2026, 1, 4),
            None,
        ]

    def test_the_domain_prefers_the_site_then_the_origin(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root, **WITH_BASE):
            section = _section()
            assert section.get_domain(OriginSite("other.example")) == "other.example"
            assert section.get_domain() == "acme.example"


class TestLanguages:
    """Without `i18n` the URLs stay in the default language, whatever is active."""

    @override_settings(**I18N)
    def test_an_active_language_leaves_the_urls_alone(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with (
            routed(root, urlconf=I18N_URLCONF, **WITH_BASE),
            translation.override("de"),
        ):
            assert _locations(_section()) == [f"{BASE}/about/"]

    @override_settings(**I18N)
    def test_i18n_lists_every_language_and_the_head_x_default(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="i18n = True\nalternates = True\nx_default = True\n",
        )
        with routed(root, urlconf=I18N_URLCONF, **WITH_BASE):
            urls = _section().get_urls(1, SITE, "https")
            head = x_default_url(hreflang_urls("/de/about/"))
        assert [url["location"] for url in urls] == [
            f"{BASE}/about/",
            f"{BASE}/de/about/",
        ]
        alternates = [
            (alt["lang_code"], alt["location"]) for alt in urls[1]["alternates"]
        ]
        assert alternates == [
            ("en", f"{BASE}/about/"),
            ("de", f"{BASE}/de/about/"),
            ("x-default", f"{BASE}/about/"),
        ]
        assert head == "/about/"

    @override_settings(**I18N)
    def test_x_default_needs_the_alternates(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="i18n = True\nx_default = True\n",
        )
        with routed(root, urlconf=I18N_URLCONF, **WITH_BASE):
            urls = _section().get_urls(1, SITE, "https")
        assert all(url["alternates"] == [] for url in urls)

    @override_settings(**I18N)
    def test_a_language_without_the_default_names_no_x_default(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="i18n = True\nalternates = True\nx_default = True\n"
            "languages = ['de']\n",
        )
        with routed(root, urlconf=I18N_URLCONF, **WITH_BASE):
            [url] = _section().get_urls(1, SITE, "https")
        assert [alt["lang_code"] for alt in url["alternates"]] == ["de"]


@pytest.fixture()
def users() -> None:
    User.objects.bulk_create(User(username=f"user{n:02}") for n in range(25))


USERS = _items(
    "@sitemap.items('people/[slug]', kwargs=lambda user: {'slug': user.username}, "
    "lastmod='last_login')\ndef people():\n    return User.objects.all()\n"
)


@pytest.mark.django_db()
@pytest.mark.usefixtures("users")
class TestQuerySetItems:
    """A `QuerySet` part reads a count, a page and an aggregate, never the table."""

    def test_a_page_reads_one_count_and_one_slice(
        self, tmp_path, django_assert_max_num_queries
    ) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("people/[slug]",), sitemap="limit = 10\n" + USERS
        )
        with routed(root, **WITH_BASE):
            section = _section()
            with django_assert_max_num_queries(2):
                assert section.paginator.num_pages == 3
                locations = _locations(section, page=2)
        assert locations == [f"{BASE}/people/user{n:02}/" for n in range(10, 20)]

    def test_an_unordered_queryset_is_ordered_by_its_key(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("people/[slug]",), sitemap=USERS)
        with routed(root):
            [part] = _section().entries().parts[1:]
        assert part.rows.query.order_by == ("pk",)

    def test_a_sliced_queryset_without_an_order_raises(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("people/[slug]",),
            sitemap=USERS.replace("User.objects.all()", "User.objects.all()[:5]"),
        )
        with routed(root), pytest.raises(TypeError, match="sliced QuerySet"):
            _section().entries()

    def test_a_queryset_part_without_a_column_reads_no_row_for_the_latest(
        self, tmp_path, django_assert_max_num_queries
    ) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("people/[slug]",),
            sitemap=USERS.replace(", lastmod='last_login'", ""),
        )
        with routed(root):
            section = _section()
            with django_assert_max_num_queries(1):
                assert section.get_latest_lastmod() is None

    def test_an_ordered_queryset_keeps_its_order(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("people/[slug]",),
            sitemap=USERS.replace("User.objects.all()", "User.objects.order_by('-pk')"),
        )
        with routed(root, **WITH_BASE):
            section = _section()
            first = _locations(section)[0]
        assert first == f"{BASE}/people/user24/"

    def test_the_latest_lastmod_is_one_aggregate(
        self, tmp_path, django_assert_num_queries
    ) -> None:
        User.objects.filter(username="user03").update(
            last_login=datetime(2026, 2, 1, tzinfo=UTC)
        )
        root = write_tree(tmp_path / "pages", pages=("people/[slug]",), sitemap=USERS)
        with routed(root):
            section = _section()
            with django_assert_num_queries(2):
                latest = section.get_latest_lastmod()
        assert latest == datetime(2026, 2, 1, tzinfo=UTC)

    def test_a_long_part_without_a_column_has_no_latest(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("people/[slug]",),
            sitemap="limit = 10\n" + USERS.replace(", lastmod='last_login'", ""),
        )
        with routed(root):
            assert _section().get_latest_lastmod() is None

    def test_an_empty_aggregate_has_no_latest(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("people/[slug]",), sitemap=USERS)
        with routed(root):
            assert _section().get_latest_lastmod() is None


class TestLatestLastmod:
    """The latest date needs every listed item dated, an empty part aside."""

    def test_every_item_dated_answers_the_latest(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=_items(
                "@sitemap.items('posts/[slug]')\ndef posts():\n"
                "    return [SitemapEntry({'slug': 'a'}, lastmod=date(2026, 1, 2)),"
                " SitemapEntry({'slug': 'b'}, lastmod=date(2026, 1, 5))]\n"
            ),
        )
        with routed(root):
            assert _section().get_latest_lastmod() == datetime(2026, 1, 5, tzinfo=UTC)

    def test_an_undated_item_drops_the_latest(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=_items(
                "@sitemap.items('posts/[slug]')\ndef posts():\n"
                "    return [SitemapEntry({'slug': 'a'}, lastmod=date(2026, 1, 2)),"
                " {'slug': 'b'}]\n"
            ),
        )
        with routed(root):
            assert _section().get_latest_lastmod() is None

    def test_a_static_route_has_no_date(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("",), sitemap="")
        with routed(root):
            assert _section().get_latest_lastmod() is None

    def test_a_section_listing_nothing_has_no_date(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap="")
        with routed(root):
            assert _section().get_latest_lastmod() is None
