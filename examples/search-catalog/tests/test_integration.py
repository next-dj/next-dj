import re

import pytest
from catalog import queries as catalog_queries
from catalog.zones import CATEGORY_ZONES, LISTING_ZONES, zone_target
from django.core.cache import cache

from next.testing import envelope_of


pytestmark = pytest.mark.django_db


PRODUCT_CARD_PATTERN = re.compile(r"data-product-card[\s\S]*?</article>")
PRODUCT_SLUG_PATTERN = re.compile(r'data-product-slug="([^"]+)"')
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

    def test_landing_renders(self, next_client, demo_data) -> None:
        """Render the landing page with featured products and category links."""
        r = next_client.get("/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Welcome to the catalog" in body
        assert "Featured" in body

    def test_landing_without_show_falls_back_to_the_default(
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
    def test_landing_show_query_param(
        self, next_client, demo_data, show, expected_count
    ) -> None:
        """Honour `?show=N` on the landing through `DQuery[int]`."""
        body = next_client.get(f"/?show={show}").content.decode()
        assert body.count("data-product-card") == expected_count

    def test_landing_reuses_product_card_css(self, next_client, demo_data) -> None:
        """Render the shared `product_card.css` once on the landing too."""
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
        """The filter_panel CSS reaches catalog pages and never the landing page."""
        catalog_body = next_client.get("/catalog/").content.decode()
        assert "filter_panel" in catalog_body

        landing_body = next_client.get("/").content.decode()
        assert "filter_panel" not in landing_body

    def test_catalog_layout_css_absent_on_landing(self, next_client, demo_data) -> None:
        """catalog/layout.css is not injected on the landing page."""
        landing_body = next_client.get("/").content.decode()
        assert "catalog/layout" not in landing_body


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
        assert envelope.op_verbs() == ["url", *["morph"] * len(LISTING_ZONES)]
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
