import json
import re
from decimal import Decimal

import pytest
from catalog import queries as catalog_queries
from catalog.demo import CATEGORIES
from catalog.landing import FAQ, LEAD_ZONE
from catalog.models import Category, Lead, Product
from catalog.zones import CATEGORY_ZONES, LISTING_ZONES, zone_target
from django.core.cache import cache

from next.testing import (
    NextClient,
    assert_metadata,
    envelope_of,
    find_anchor,
    find_form,
    init_payload,
    parse_sitemap,
)


pytestmark = pytest.mark.django_db


PRODUCT_CARD_PATTERN = re.compile(r"data-product-card[\s\S]*?</article>")
PRODUCT_SLUG_PATTERN = re.compile(r'data-product-slug="([^"]+)"')
JSONLD_PATTERN = re.compile(r'<script type="application/ld\+json">(.*?)</script>')
KEY_PATTERN = re.compile(r'data-next-key="([^"]+)"')
FILTER_FORM_PATTERN = re.compile(r"<form method=\"get\"[\s\S]*?>")
MORE_ZONE_PATTERN = re.compile(
    r'<div data-next-zone="catalog-more"[^>]*>([\s\S]*?)</div>'
)

LISTING_TARGET = zone_target(LISTING_ZONES)
CATEGORY_TARGET = zone_target(CATEGORY_ZONES)


def _filter_form_tag(body: str) -> str:
    """Return the opening tag of the filter panel form."""
    return FILTER_FORM_PATTERN.search(body).group(0)


def _product_card_section(body: str) -> str:
    """Concatenate every product-card snippet from the rendered body."""
    return "\n".join(PRODUCT_CARD_PATTERN.findall(body))


def _slug_set(body: str) -> set[str]:
    """Return the set of product slugs rendered as cards in the body."""
    return set(PRODUCT_SLUG_PATTERN.findall(body))


def _more_zone(body: str) -> str:
    """Return the rendered body of the `catalog-more` zone."""
    return MORE_ZONE_PATTERN.search(body).group(1)


def _cache_keys() -> list[str]:
    """Return every cache key currently stored under the search prefix."""
    return [k for k in cache._cache if catalog_queries.CACHE_KEY_PREFIX in k]


class TestRouting:
    """Cover routing, status codes, and CSS dedup."""

    def test_home_page_renders(self, next_client, demo_data) -> None:
        """Render the home page with featured products and category links."""
        r = next_client.get("/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Welcome to the catalog" in body
        assert "Featured" in body

    def test_home_page_without_show_falls_back_to_the_default(
        self, next_client, demo_data
    ) -> None:
        """Render the `DQuery[int]` default when no `?show` is supplied."""
        body = next_client.get("/").content.decode()
        assert body.count("data-product-card") == 3

    @pytest.mark.parametrize(
        ("show", "expected_count"),
        [(1, 1), (6, 6), (999, 12), (0, 1)],
        ids=(
            "single_card",
            "six_cards",
            "above_max_clamps_to_twelve",
            "zero_clamps_to_one",
        ),
    )
    def test_home_page_show_query_param(
        self, next_client, demo_data, show, expected_count
    ) -> None:
        """Honour `?show=N` on the home page through `DQuery[int]`."""
        body = next_client.get(f"/?show={show}").content.decode()
        assert body.count("data-product-card") == expected_count

    def test_home_page_reuses_product_card_css(self, next_client, demo_data) -> None:
        """Render the shared `product_card.css` once on the home page too."""
        body = next_client.get("/").content.decode()
        assert body.count("data-product-card") == 3
        assert body.count("product_card.css") == 1

    def test_listing_renders(self, next_client, demo_data) -> None:
        """Render the all-products listing with the configured page size."""
        r = next_client.get("/catalog/")
        assert r.status_code == 200
        body = r.content.decode()
        assert body.count("data-product-card") == 6
        assert "All products" in body

    def test_product_card_css_dedup(self, next_client, demo_data) -> None:
        """Render the product-card stylesheet exactly once on the listing page."""
        body = next_client.get("/catalog/").content.decode()
        assert body.count("data-product-card") == 6
        link_count = body.count("product_card.css")
        assert link_count == 1, (
            f"expected one product_card.css link, found {link_count}"
        )

    @pytest.mark.parametrize(
        ("url", "expected_status"),
        [
            ("/catalog/electronics/", 200),
            ("/catalog/books/", 200),
            ("/catalog/unknown/", 404),
            ("/catalog/electronics/iphone-15/", 200),
            ("/catalog/electronics/missing-slug/", 404),
        ],
        ids=(
            "known_category",
            "second_known_category",
            "unknown_category",
            "known_product",
            "unknown_product",
        ),
    )
    def test_routing_status(self, next_client, demo_data, url, expected_status) -> None:
        """Return the expected status code for every routing scenario."""
        assert next_client.get(url).status_code == expected_status

    def test_product_detail_breadcrumb(self, next_client, demo_data) -> None:
        """Show the product name on the detail page."""
        r = next_client.get("/catalog/electronics/iphone-15/")
        assert r.status_code == 200
        assert "iPhone 15" in r.content.decode()

    def test_filter_panel_scoped_to_catalog(self, next_client, demo_data) -> None:
        """The filter_panel CSS reaches catalog pages and never the home page."""
        catalog_body = next_client.get("/catalog/").content.decode()
        assert "filter_panel" in catalog_body

        home_body = next_client.get("/").content.decode()
        assert "filter_panel" not in home_body

    def test_catalog_layout_css_absent_on_home_page(
        self, next_client, demo_data
    ) -> None:
        """catalog/layout.css is not injected on the home page."""
        home_body = next_client.get("/").content.decode()
        assert "catalog/layout" not in home_body


class TestFilters:
    """Cover query filtering across every supported wire format."""

    @pytest.mark.parametrize(
        ("query", "expected_brands"),
        [
            ("?brand=Acme", {"Acme"}),
            ("?brand[]=Acme", {"Acme"}),
            ("?brand=Acme&brand=Globex", {"Acme", "Globex"}),
            ("?brand=Acme,Globex", {"Acme", "Globex"}),
            ("?brand[]=Acme&brand[]=Globex", {"Acme", "Globex"}),
        ],
        ids=(
            "single_plain_key",
            "single_bracket_key",
            "repeated_plain_key",
            "comma_delimited_value",
            "repeated_bracket_key",
        ),
    )
    def test_filter_by_brand_keeps_only_listed_brands(
        self, next_client, demo_data, query, expected_brands
    ) -> None:
        """Restrict the listing to the requested brands across every wire format."""
        cards = _product_card_section(
            next_client.get(f"/catalog/{query}").content.decode()
        )
        excluded = {"Acme", "Globex", "Initech", "Hooli"} - expected_brands
        for brand in excluded:
            assert brand not in cards
        for brand in expected_brands:
            assert brand in cards

    def test_filter_by_price_range(self, next_client, demo_data) -> None:
        """Restrict listing to prices inside the requested range."""
        r = next_client.get("/catalog/?price_min=100&price_max=300")
        assert r.status_code == 200

    def test_in_stock_filter(self, next_client, demo_data) -> None:
        """Apply the in-stock filter without errors."""
        r = next_client.get("/catalog/?in_stock=1")
        assert r.status_code == 200
        assert "out of stock" not in _product_card_section(r.content.decode()).lower()

    def test_search_query_matches_product_name(self, next_client, demo_data) -> None:
        """Match products whose name contains the search term."""
        r = next_client.get("/catalog/?q=iPhone")
        assert r.status_code == 200
        assert "iPhone 15" in r.content.decode()

    def test_pagination_returns_distinct_pages(self, next_client, demo_data) -> None:
        """Return non-overlapping product slugs for different page numbers."""
        slugs_page1 = _slug_set(next_client.get("/catalog/?page=1").content.decode())
        slugs_page2 = _slug_set(next_client.get("/catalog/?page=2").content.decode())
        assert slugs_page1
        assert slugs_page2
        assert not slugs_page1 & slugs_page2


class TestActiveFilterChip:
    """Cover the active-filter chip rendered by the context processor."""

    def test_chip_renders_with_class_and_data_attribute(
        self, next_client, demo_data
    ) -> None:
        """Render a chip with the active-filter class and a data attribute."""
        body = next_client.get("/catalog/?brand=Acme").content.decode()
        assert "active-filter" in body
        assert 'data-active-filter="brand"' in body
        assert "Brand Acme" in body

    def test_no_chip_on_default_sort(self, next_client, demo_data) -> None:
        """Skip chip rendering when only the default sort key is present."""
        body = next_client.get("/catalog/?sort=newest").content.decode()
        assert "data-active-filter" not in body


class TestInheritContext:
    """Cover the `inherit_context=True` pathway."""

    def test_product_detail_uses_inherited_category(
        self, next_client, demo_data
    ) -> None:
        """Surface the inherited Category instance on the product detail page."""
        body = next_client.get("/catalog/electronics/iphone-15/").content.decode()
        assert "Electronics" in body
        assert "data-breadcrumb" in body


class TestCacheHit:
    """Cover the LocMem cache hit path of `cached_search`."""

    def test_two_identical_requests_share_one_cache_key(
        self, next_client, demo_data
    ) -> None:
        """Store one cache entry when the same filters arrive twice in a row."""
        next_client.get("/catalog/?brand=Acme")
        next_client.get("/catalog/?brand=Acme")
        assert len(_cache_keys()) == 1

    def test_distinct_filters_produce_distinct_keys(
        self, next_client, demo_data
    ) -> None:
        """Store one cache entry per distinct filter set."""
        next_client.get("/catalog/?brand=Acme")
        next_client.get("/catalog/?brand=Globex")
        assert len(_cache_keys()) == 2


class TestResultsZone:
    """Cover the catalog-results zone that drives auto-submit and pagination."""

    def test_listing_wraps_results_in_a_named_zone(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get("/catalog/").content.decode()
        assert 'data-next-zone="catalog-results"' in body
        assert '<ul data-next-zone="catalog-results"' in body

    def test_filter_form_carries_auto_submit_attributes(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get("/catalog/").content.decode()
        form_tag = _filter_form_tag(body)
        assert f'data-next-target="{LISTING_TARGET}"' in form_tag
        assert 'data-next-trigger="input"' in form_tag
        assert 'data-next-debounce="300"' in form_tag
        assert 'data-next-trigger="change"' in body

    def test_zone_request_morphs_only_the_results(self, next_client, demo_data) -> None:
        response = next_client.get_zones("/catalog/", "catalog-results")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["catalog-results"]
        html = envelope.html_for_zone("catalog-results")
        assert html.startswith('<ul data-next-zone="catalog-results">')
        assert "All products" not in html

    def test_zone_request_narrows_to_the_search_term(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.get_zones("/catalog/?q=iPhone", "catalog-results")
        )
        html = envelope.html_for_zone("catalog-results")
        assert "iPhone 15" in html

    def test_zone_request_stamps_the_partial_vary_headers(
        self, next_client, demo_data
    ) -> None:
        response = next_client.get_zones("/catalog/", "catalog-results")
        vary = response["Vary"]
        assert "X-Next-Zone" in vary
        assert "X-Next-Merge" in vary

    def test_full_page_declares_the_same_vary_set(self, next_client, demo_data) -> None:
        full = next_client.get("/catalog/")
        zoned = next_client.get_zones("/catalog/", "catalog-results")
        declared = {name.strip() for name in zoned["Vary"].split(",")}
        assert declared <= {name.strip() for name in full["Vary"].split(",")}


class TestInfiniteScrollAppend:
    """Cover the revealed sentinel and the append merge it drives."""

    def test_first_page_renders_a_sentinel_link(self, next_client, demo_data) -> None:
        body = next_client.get("/catalog/").content.decode()
        assert 'id="results-sentinel"' in body
        assert 'data-next-merge="append"' in body
        assert 'data-next-lazy="revealed"' in body
        assert 'data-next-target="catalog-results,catalog-more"' in body
        assert "page=2" in body

    def test_sentinel_lives_outside_the_results_zone(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get("/catalog/").content.decode()
        results = body.split('data-next-zone="catalog-results"', 1)[1]
        zone_body = results.split("</ul>", 1)[0]
        assert "results-sentinel" not in zone_body
        assert 'data-next-zone="catalog-more"' in body

    def test_append_grows_results_and_replaces_the_sentinel(
        self, next_client, demo_data
    ) -> None:
        response = next_client.get_zones(
            "/catalog/?page=2",
            "catalog-results,catalog-more",
            HTTP_X_NEXT_MERGE="append",
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["append", "append"]
        assert envelope.zone_targets() == ["catalog-results", "catalog-more"]

    def test_append_to_results_carries_rows_only_not_the_sentinel(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.get_zones(
                "/catalog/?page=2",
                "catalog-results,catalog-more",
                HTTP_X_NEXT_MERGE="append",
            )
        )
        results_html = envelope.html_for_zone("catalog-results")
        assert "results-sentinel" not in results_html
        assert KEY_PATTERN.findall(results_html)
        more_html = envelope.html_for_zone("catalog-more")
        assert 'id="results-sentinel"' in more_html
        assert "page=3" in more_html

    def test_appended_rows_do_not_overlap_the_first_page(
        self, next_client, demo_data
    ) -> None:
        first = next_client.get_zones("/catalog/", "catalog-results")
        second = next_client.get_zones(
            "/catalog/?page=2", "catalog-results", HTTP_X_NEXT_MERGE="append"
        )
        keys_one = set(
            KEY_PATTERN.findall(envelope_of(first).html_for_zone("catalog-results"))
        )
        keys_two = set(
            KEY_PATTERN.findall(envelope_of(second).html_for_zone("catalog-results"))
        )
        assert keys_one
        assert keys_two
        assert not keys_one & keys_two

    def test_last_page_swaps_the_link_for_an_inert_end_marker(
        self, next_client, demo_data
    ) -> None:
        more = _more_zone(next_client.get("/catalog/?page=999").content.decode())
        assert 'id="results-sentinel"' in more
        assert "data-catalog-end" in more
        assert "<a" not in more
        assert "data-next-merge" not in more
        assert "data-next-lazy" not in more

    def test_last_page_append_keeps_the_sentinel_id_on_the_end_marker(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.get_zones(
                "/catalog/?page=999",
                "catalog-results,catalog-more",
                HTTP_X_NEXT_MERGE="append",
            )
        )
        more_html = envelope.html_for_zone("catalog-more")
        assert 'id="results-sentinel"' in more_html
        assert "data-catalog-end" in more_html
        assert "data-next-merge" not in more_html
        assert "page=" not in more_html

    def test_changing_the_query_re_morphs_both_zones(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.get_zones("/catalog/?q=novel", "catalog-results,catalog-more")
        )
        assert envelope.op_verbs() == ["morph", "morph"]


class TestPresetFilterPushUrl:
    """Applying a preset is a discrete jump that pushes a history entry."""

    def test_listing_renders_the_preset_bar(self, next_client, demo_data) -> None:
        body = next_client.get("/catalog/").content.decode()
        assert "data-preset-bar" in body
        assert 'name="preset" value="cheapest"' in body

    def test_partial_apply_pushes_url_and_morphs_every_listing_zone(
        self, next_client, demo_data
    ) -> None:
        response = next_client.post_action(
            "preset_filter_form",
            {"preset": "cheapest"},
            origin="/catalog/",
            partial=True,
            zones=LISTING_TARGET,
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["url", "meta", *["morph"] * len(LISTING_ZONES)]
        assert envelope.zone_targets() == list(LISTING_ZONES)

    def test_preset_bar_targets_the_full_listing_zone_set(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get("/catalog/").content.decode()
        preset_form = body.split("data-preset-bar", 1)[0].rsplit("<form", 1)[1]
        assert f'data-next-target="{LISTING_TARGET}"' in preset_form

    def test_preset_apply_refreshes_the_count_and_the_chips(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.post_action(
                "preset_filter_form",
                {"preset": "in_stock"},
                origin="/catalog/",
                partial=True,
                zones=LISTING_TARGET,
            )
        )
        assert "products" in envelope.html_for_zone("catalog-count")
        assert 'data-active-filter="in_stock"' in envelope.html_for_zone(
            "catalog-chips"
        )

    def test_url_op_pushes_the_canonical_same_site_href(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.post_action(
                "preset_filter_form",
                {"preset": "cheapest"},
                origin="/catalog/",
                partial=True,
                zones=LISTING_TARGET,
            )
        )
        url_op = next(op for op in envelope.ops if op["op"] == "url")
        assert url_op["action"] == "push"
        assert url_op["href"] == "/catalog/?sort=price_asc"
        assert url_op["href"].startswith("/catalog/")

    def test_in_stock_preset_morph_drops_out_of_stock_products(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.post_action(
                "preset_filter_form",
                {"preset": "in_stock"},
                origin="/catalog/",
                partial=True,
                zones=LISTING_TARGET,
            )
        )
        results = envelope.html_for_zone("catalog-results")
        assert "out of stock" not in results.lower()

    def test_morphed_sentinel_carries_the_preset_querystring(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.post_action(
                "preset_filter_form",
                {"preset": "cheapest"},
                origin="/catalog/",
                partial=True,
                zones=LISTING_TARGET,
            )
        )
        more = envelope.html_for_zone("catalog-more")
        assert "sort=price_asc" in more

    def test_non_partial_apply_falls_back_to_a_redirect(
        self, next_client, demo_data
    ) -> None:
        response = next_client.post_action("preset_filter_form", {"preset": "newest"})
        assert response.status_code == 302
        assert response["Location"] == "/catalog/?sort=newest"


class TestFilterZoneScope:
    """The live filter only targets zones the current page actually declares."""

    def test_listing_declares_every_targeted_zone(self, next_client, demo_data) -> None:
        body = next_client.get("/catalog/").content.decode()
        for zone in LISTING_ZONES:
            assert f'data-next-zone="{zone}"' in body

    def test_category_form_drops_the_append_sentinel_zone(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get("/catalog/electronics/").content.decode()
        assert f'data-next-target="{CATEGORY_TARGET}"' in _filter_form_tag(body)
        assert "catalog-more" not in body
        for zone in CATEGORY_ZONES:
            assert f'data-next-zone="{zone}"' in body

    def test_product_detail_filter_form_makes_a_plain_get(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get("/catalog/electronics/iphone-15/").content.decode()
        form_tag = _filter_form_tag(body)
        assert 'method="get"' in form_tag
        assert "data-next-target" not in form_tag
        assert "data-next-trigger" not in body
        assert "data-next-debounce" not in body

    def test_category_zone_request_morphs_the_declared_set(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.get_zones("/catalog/electronics/?q=iPhone", CATEGORY_TARGET)
        )
        assert envelope.zone_targets() == list(CATEGORY_ZONES)
        assert envelope.op_verbs() == ["morph"] * len(CATEGORY_ZONES)
        results = envelope.html_for_zone("catalog-results")
        assert results.startswith('<ul data-next-zone="catalog-results">')
        assert KEY_PATTERN.findall(results)


class TestLiveFilterMorphsTheWholeListing:
    """A live filter GET refreshes the count, the pager, and the chip strip."""

    def test_count_zone_follows_the_search_term(self, next_client, demo_data) -> None:
        envelope = envelope_of(
            next_client.get_zones("/catalog/?q=iPhone", LISTING_TARGET)
        )
        assert "1 products" in envelope.html_for_zone("catalog-count")

    def test_pager_zone_collapses_to_a_single_page(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.get_zones("/catalog/?q=iPhone", LISTING_TARGET)
        )
        pager = envelope.html_for_zone("catalog-pager")
        assert "Page 1 of 1" in pager
        assert "page=2" not in pager

    def test_chip_zone_gains_the_search_chip(self, next_client, demo_data) -> None:
        envelope = envelope_of(
            next_client.get_zones("/catalog/?q=iPhone", LISTING_TARGET)
        )
        chips = envelope.html_for_zone("catalog-chips")
        assert 'data-active-filter="q"' in chips
        assert "Search iPhone" in chips

    def test_chip_zone_renders_empty_without_filters(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(next_client.get_zones("/catalog/", LISTING_TARGET))
        chips = envelope.html_for_zone("catalog-chips")
        assert chips.startswith('<div data-next-zone="catalog-chips">')
        assert "active-filter" not in chips


class TestPageMetadata:
    """One title template spans the home page, the listings and the product page."""

    def test_home_page_uses_the_site_default(self, next_client, demo_data) -> None:
        assert_metadata(
            next_client.get("/"),
            title="next.dj — Search catalog",
            description="Faceted search over a demo storefront, built on next.dj.",
            canonical=None,
            robots=None,
        )

    def test_listing_declares_a_static_title_and_a_self_canonical(
        self, next_client, demo_data
    ) -> None:
        assert_metadata(
            next_client.get("/catalog/?q=item&sort=price_asc"),
            title="All products · next.dj catalog",
            canonical="https://catalog.example/catalog/",
        )

    def test_category_title_comes_from_the_inherited_category(
        self, next_client, demo_data
    ) -> None:
        assert_metadata(
            next_client.get("/catalog/electronics/?brand=Acme&page=2"),
            title="Electronics · next.dj catalog",
            canonical="https://catalog.example/catalog/electronics/?page=2",
        )

    def test_the_canonical_drops_the_first_page(self, next_client, demo_data) -> None:
        assert_metadata(
            next_client.get("/catalog/electronics/?page=1"),
            canonical="https://catalog.example/catalog/electronics/",
        )

    def test_product_title_and_description_come_from_the_product(
        self, next_client, demo_data
    ) -> None:
        assert_metadata(
            next_client.get("/catalog/electronics/iphone-15/"),
            title="iPhone 15 · next.dj catalog",
            description="Flagship handset used by routing tests.",
        )

    def test_a_preset_renames_the_tab_through_the_page_template(
        self, next_client, demo_data
    ) -> None:
        envelope = envelope_of(
            next_client.post_action(
                "preset_filter_form",
                {"preset": "cheapest"},
                origin="/catalog/",
                partial=True,
                zones=LISTING_TARGET,
            )
        )
        meta_op = next(op for op in envelope.ops if op["op"] == "meta")
        assert meta_op["title"] == "Cheapest first · next.dj catalog"


def _locs(response) -> set[str]:
    return {url.loc for url in parse_sitemap(response)}


class TestSitemap:
    """`sitemap.py` lists the listings over the rows and caches the document."""

    def test_sitemap_lists_static_routes_landings_categories_and_products(
        self, next_client, demo_data
    ) -> None:
        response = next_client.get("/sitemap.xml")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml"
        locs = _locs(response)
        assert {"https://catalog.example/", "https://catalog.example/catalog/"} <= locs
        slugs = [slug for slug, _name, _tagline in CATEGORIES]
        assert {f"https://catalog.example/catalog/{slug}/" for slug in slugs} <= locs
        assert {f"https://catalog.example/shop/{slug}/" for slug in slugs} <= locs
        assert "https://catalog.example/catalog/electronics/iphone-15/" in locs
        assert len(locs) == 2 + 2 * Category.objects.count() + Product.objects.count()

    def test_the_document_is_cached_for_five_minutes(
        self, next_client, demo_data
    ) -> None:
        first = next_client.get("/sitemap.xml")
        assert first["Cache-Control"] == "public, max-age=300"
        Product.objects.create(
            category=Category.objects.get(slug="books"),
            slug="late-arrival",
            name="Late arrival",
            brand="Acme",
            price=Decimal("1.00"),
        )
        second = next_client.get("/sitemap.xml")
        assert second.content == first.content
        assert "late-arrival" not in second.content.decode()

    def test_an_empty_catalog_still_lists_the_static_routes(self, next_client) -> None:
        assert _locs(next_client.get("/sitemap.xml")) == {
            "https://catalog.example/",
            "https://catalog.example/catalog/",
        }


GRANT_ALL = "2:analytics|marketing:1700000000"
FAQ_NODE = {
    "@type": "FAQPage",
    "mainEntity": [
        {
            "@type": "Question",
            "name": name,
            "acceptedAnswer": {"@type": "Answer", "text": answer},
        }
        for name, answer in FAQ
    ],
}


def _script_names(body: str) -> list[str]:
    return [entry["name"] for entry in init_payload(body).get("$scripts", [])]


def _product_node(body: str) -> dict[str, object]:
    graph = json.loads(JSONLD_PATTERN.search(body).group(1))["@graph"]
    return next(node for node in graph if node["@type"] == "Product")


def _head(body: str) -> str:
    return body[: body.index("</head>")]


@pytest.fixture()
def lamps() -> Category:
    """Seed a category with two lamps on sale and one sold out."""
    category = Category.objects.create(
        slug="lamps", name="Lamps", tagline="Warm light for every corner of the house."
    )
    for slug, name, price, in_stock in (
        ("desk", "Desk lamp", "40.00", True),
        ("floor", "Floor lamp", "90.00", True),
        ("wall", "Wall lamp", "10.00", False),
    ):
        Product.objects.create(
            category=category,
            slug=slug,
            name=name,
            brand="Lumen",
            price=Decimal(price),
            in_stock=in_stock,
        )
    return category


class TestProductStructuredData:
    """The product page publishes the product and its offer as a plain dict."""

    def test_an_offer_carries_price_currency_and_stock(
        self, next_client, lamps
    ) -> None:
        body = next_client.get("/catalog/lamps/desk/").content.decode()
        assert _product_node(body) == {
            "@type": "Product",
            "@id": "https://catalog.example/catalog/lamps/desk/#product",
            "name": "Desk lamp",
            "sku": "lamps-desk",
            "brand": {"@type": "Brand", "name": "Lumen"},
            "offers": {
                "@type": "Offer",
                "price": "40.00",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock",
            },
        }

    def test_a_sold_out_product_says_so(self, next_client, lamps) -> None:
        body = next_client.get("/catalog/lamps/wall/").content.decode()
        offer = _product_node(body)["offers"]
        assert offer["availability"] == "https://schema.org/OutOfStock"


class TestLanding:
    """A category landing sells its cheapest stock and is cached for everyone."""

    def test_the_landing_offers_the_cheapest_stock_and_answers_questions(
        self, next_client, lamps
    ) -> None:
        response = next_client.get("/shop/lamps/")
        body = response.content.decode()
        assert response.status_code == 200
        assert _slug_set(body) == {"desk", "floor"}
        assert all(question in body for question, _answer in FAQ)
        assert find_anchor(body, href="/catalog/lamps/", text="Shop the range")

    def test_the_head_publishes_its_answers_as_structured_data(
        self, next_client, lamps
    ) -> None:
        assert_metadata(
            next_client.get("/shop/lamps/"),
            title="Lamps deals · next.dj catalog",
            description="Warm light for every corner of the house.",
            canonical="https://catalog.example/shop/lamps/",
            robots=None,
            jsonld=[FAQ_NODE],
        )

    def test_a_category_without_tagline_keeps_the_site_description(
        self, next_client
    ) -> None:
        Category.objects.create(slug="garden", name="Garden")
        assert_metadata(
            next_client.get("/shop/garden/"),
            description="Faceted search over a demo storefront, built on next.dj.",
        )

    def test_an_unknown_category_is_not_found(self, next_client) -> None:
        assert next_client.get("/shop/nothing/").status_code == 404

    def test_a_cdn_may_hold_the_landing(self, next_client, lamps) -> None:
        response = next_client.get("/shop/lamps/")
        assert response["Cache-Control"] == (
            "public, max-age=300, stale-while-revalidate=60"
        )
        assert "csrftoken" not in response.cookies
        assert "Cookie" not in response["Vary"]
        assert init_payload(response.content.decode())["$csrf"] == {
            "header": "X-Csrftoken",
            "url": "/_next/csrf/",
        }


class TestScriptsBehindConsent:
    """`scripts.py` declares the two tags, and consent decides who renders them."""

    def test_an_undecided_visitor_gets_them_through_the_manifest(
        self, next_client, lamps
    ) -> None:
        body = next_client.get("/shop/lamps/").content.decode()
        assert 'data-next-script="google-analytics"' not in body
        assert _script_names(body) == ["google-analytics", "meta-pixel"]
        entry = init_payload(body)["$scripts"][0]
        assert entry["src"] == "/static/catalog/tags/ga4.js?v=v1"
        assert entry["category"] == "analytics"
        assert entry["attrs"] == {"data-measurement-id": "G-DEMO0000"}

    def test_a_granted_visitor_gets_the_tags_from_the_server(
        self, next_client, lamps
    ) -> None:
        next_client.cookies["next_consent"] = GRANT_ALL
        response = next_client.get("/catalog/")
        head = _head(response.content.decode())
        assert '<script src="/static/catalog/tags/ga4.js?v=v1" defer' in head
        assert 'data-measurement-id="G-DEMO0000"' in head
        assert "Cookie" in response["Vary"]

    def test_the_cached_landing_ignores_a_granted_cookie(
        self, next_client, lamps
    ) -> None:
        next_client.cookies["next_consent"] = GRANT_ALL
        body = next_client.get("/shop/lamps/").content.decode()
        assert 'data-next-script="google-analytics"' not in body
        assert _script_names(body) == ["google-analytics", "meta-pixel"]
        assert '<template data-next-consented="marketing">' in body
        assert "data-landing-video-placeholder" in body

    def test_only_the_landing_loads_the_pixel(self, next_client, lamps) -> None:
        listing = next_client.get("/catalog/").content.decode()
        assert _script_names(listing) == ["google-analytics"]

    def test_the_banner_ships_hidden_with_its_script(self, next_client, lamps) -> None:
        body = next_client.get("/shop/lamps/").content.decode()
        assert "<section data-consent-banner hidden" in body
        assert '<script src="/static/next/components/consent_banner.js?v=v1">' in body
        assert init_payload(body)["$consent"]["categories"] == [
            "necessary",
            "analytics",
            "marketing",
        ]


class TestLandingSignup:
    """The cached landing signs visitors up through a runtime-only form."""

    def _submit(self, next_client, email: str, *, partial: bool = True):
        return next_client.post_action(
            "launch_code_form",
            {"email": email},
            origin="/shop/lamps/",
            partial=partial,
            zones=LEAD_ZONE,
        )

    def test_the_form_renders_without_a_csrf_token(self, next_client, lamps) -> None:
        body = next_client.get("/shop/lamps/").content.decode()
        form = find_form(body, action=next_client.get_action_url("launch_code_form"))
        assert "csrfmiddlewaretoken" not in form
        assert "This sign-up needs JavaScript." in body

    def test_a_bare_submit_without_the_token_is_refused(self, lamps) -> None:
        client = NextClient(enforce_csrf_checks=True)
        response = client.post_action(
            "launch_code_form", {"email": "ada@example.com"}, origin="/shop/lamps/"
        )
        assert response.status_code == 403
        assert not Lead.objects.exists()

    def test_a_runtime_submit_thanks_in_place(self, next_client, lamps) -> None:
        envelope = envelope_of(self._submit(next_client, "ada@example.com"))
        lead = Lead.objects.get()
        assert (lead.email, lead.category) == ("ada@example.com", lamps)
        morph = next(op for op in envelope.ops if op["op"] == "morph")
        assert "Thanks, the code is on its way to ada@example.com." in morph["html"]

    def test_a_submit_without_the_partial_switch_redirects_back(
        self, next_client, lamps
    ) -> None:
        response = self._submit(next_client, "bob@example.com", partial=False)
        assert response.status_code == 303
        assert response["Location"] == "/shop/lamps/"
        assert Lead.objects.get().email == "bob@example.com"

    def test_an_invalid_email_stores_nothing(self, next_client, lamps) -> None:
        response = self._submit(next_client, "not-an-email")
        assert response.status_code == 200
        assert not Lead.objects.exists()
