import re

import pytest
from catalog import queries as catalog_queries
from django.core.cache import cache

from next.testing import envelope_of


PRODUCT_CARD_PATTERN = re.compile(r"data-product-card[\s\S]*?</article>")
PRODUCT_SLUG_PATTERN = re.compile(r'data-product-slug="([^"]+)"')
KEY_PATTERN = re.compile(r'data-next-key="([^"]+)"')


def _product_card_section(body: str) -> str:
    """Concatenate every product-card snippet from the rendered body."""
    return "\n".join(PRODUCT_CARD_PATTERN.findall(body))


def _slug_set(body: str) -> set[str]:
    """Return the set of product slugs rendered as cards in the body."""
    return set(PRODUCT_SLUG_PATTERN.findall(body))


def _cache_keys() -> list[str]:
    """Return every cache key currently stored under the search prefix."""
    return [k for k in cache._cache if catalog_queries.CACHE_KEY_PREFIX in k]


class TestRouting:
    """Cover routing, status codes, and CSS dedup."""

    def test_landing_renders(self, client, catalog_db) -> None:
        """Render the landing page with featured products and category links."""
        r = client.get("/")
        assert r.status_code == 200
        body = r.content.decode()
        assert "Welcome to the catalog" in body
        assert "Featured" in body

    @pytest.mark.parametrize(
        ("show", "expected_count"),
        [
            (None, 3),
            (1, 1),
            (6, 6),
            (999, 12),
            (0, 1),
        ],
    )
    def test_landing_show_query_param(
        self,
        client,
        catalog_db,
        show,
        expected_count,
    ) -> None:
        """Honour `?show=N` on the landing through `DQuery[int]`."""
        url = "/" if show is None else f"/?show={show}"
        body = client.get(url).content.decode()
        assert body.count("data-product-card") == expected_count

    def test_landing_reuses_product_card_css(
        self,
        client,
        catalog_db,
    ) -> None:
        """Render the shared `product_card.css` once on the landing too."""
        body = client.get("/").content.decode()
        assert body.count("data-product-card") == 3
        assert body.count("product_card.css") == 1

    def test_listing_renders(self, client, catalog_db) -> None:
        """Render the all-products listing with the configured page size."""
        r = client.get("/catalog/")
        assert r.status_code == 200
        body = r.content.decode()
        assert body.count("data-product-card") == 6
        assert "All products" in body

    def test_product_card_css_dedup(self, client, catalog_db) -> None:
        """Render the product-card stylesheet exactly once on the listing page."""
        body = client.get("/catalog/").content.decode()
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
    )
    def test_routing_status(self, client, catalog_db, url, expected_status) -> None:
        """Return the expected status code for every routing scenario."""
        assert client.get(url).status_code == expected_status

    def test_product_detail_breadcrumb(self, client, catalog_db) -> None:
        """Show the product name on the detail page."""
        r = client.get("/catalog/electronics/iphone-15/")
        assert r.status_code == 200
        assert "iPhone 15" in r.content.decode()

    def test_filter_panel_scoped_to_catalog(self, client, catalog_db) -> None:
        """filter_panel CSS is present on catalog pages but absent on the landing page."""
        catalog_body = client.get("/catalog/").content.decode()
        assert "filter_panel" in catalog_body

        landing_body = client.get("/").content.decode()
        assert "filter_panel" not in landing_body

    def test_catalog_layout_css_absent_on_landing(self, client, catalog_db) -> None:
        """catalog/layout.css is not injected on the landing page."""
        landing_body = client.get("/").content.decode()
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
    )
    def test_filter_by_brand_keeps_only_listed_brands(
        self,
        client,
        catalog_db,
        query,
        expected_brands,
    ) -> None:
        """Restrict the listing to the requested brands across every wire format."""
        cards = _product_card_section(
            client.get(f"/catalog/{query}").content.decode(),
        )
        excluded = {"Acme", "Globex", "Initech", "Hooli"} - expected_brands
        for brand in excluded:
            assert brand not in cards
        for brand in expected_brands:
            assert brand in cards

    def test_filter_by_price_range(self, client, catalog_db) -> None:
        """Restrict listing to prices inside the requested range."""
        r = client.get("/catalog/?price_min=100&price_max=300")
        assert r.status_code == 200

    def test_in_stock_filter(self, client, catalog_db) -> None:
        """Apply the in-stock filter without errors."""
        r = client.get("/catalog/?in_stock=1")
        assert r.status_code == 200
        assert "out of stock" not in _product_card_section(r.content.decode()).lower()

    def test_search_query_matches_product_name(
        self,
        client,
        catalog_db,
    ) -> None:
        """Match products whose name contains the search term."""
        r = client.get("/catalog/?q=iPhone")
        assert r.status_code == 200
        assert "iPhone 15" in r.content.decode()

    def test_pagination_returns_distinct_pages(
        self,
        client,
        catalog_db,
    ) -> None:
        """Return non-overlapping product slugs for different page numbers."""
        slugs_page1 = _slug_set(client.get("/catalog/?page=1").content.decode())
        slugs_page2 = _slug_set(client.get("/catalog/?page=2").content.decode())
        assert slugs_page1
        assert slugs_page2
        assert not slugs_page1 & slugs_page2


class TestActiveFilterChip:
    """Cover the active-filter chip rendered by the context processor."""

    def test_chip_renders_with_class_and_data_attribute(
        self,
        client,
        catalog_db,
    ) -> None:
        """Render a chip with the active-filter class and a data attribute."""
        body = client.get("/catalog/?brand=Acme").content.decode()
        assert "active-filter" in body
        assert 'data-active-filter="brand"' in body
        assert "Brand Acme" in body

    def test_no_chip_on_default_sort(
        self,
        client,
        catalog_db,
    ) -> None:
        """Skip chip rendering when only the default sort key is present."""
        body = client.get("/catalog/?sort=newest").content.decode()
        assert "data-active-filter" not in body


class TestInheritContext:
    """Cover the `inherit_context=True` pathway."""

    def test_product_detail_uses_inherited_category(
        self,
        client,
        catalog_db,
    ) -> None:
        """Surface the inherited Category instance on the product detail page."""
        body = client.get("/catalog/electronics/iphone-15/").content.decode()
        assert "Electronics" in body
        assert "data-breadcrumb" in body


class TestCacheHit:
    """Cover the LocMem cache hit path of `cached_search`."""

    def test_two_identical_requests_share_one_cache_key(
        self,
        client,
        catalog_db,
    ) -> None:
        """Store one cache entry when the same filters arrive twice in a row."""
        client.get("/catalog/?brand=Acme")
        client.get("/catalog/?brand=Acme")
        assert len(_cache_keys()) == 1

    def test_distinct_filters_produce_distinct_keys(
        self,
        client,
        catalog_db,
    ) -> None:
        """Store one cache entry per distinct filter set."""
        client.get("/catalog/?brand=Acme")
        client.get("/catalog/?brand=Globex")
        assert len(_cache_keys()) == 2


class TestResultsZone:
    """Cover the catalog-results zone that drives auto-submit and pagination."""

    def test_listing_wraps_results_in_a_named_zone(self, client, catalog_db) -> None:
        body = client.get("/catalog/").content.decode()
        assert 'data-next-zone="catalog-results"' in body
        assert '<ul data-next-zone="catalog-results"' in body

    def test_filter_form_carries_auto_submit_attributes(
        self, client, catalog_db
    ) -> None:
        body = client.get("/catalog/").content.decode()
        assert 'data-next-target="catalog-results"' in body
        assert 'data-next-trigger="input"' in body
        assert 'data-next-debounce="300"' in body
        assert 'data-next-trigger="change"' in body

    def test_zone_request_morphs_only_the_results(self, client, catalog_db) -> None:
        response = client.get_zones("/catalog/", "catalog-results")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["catalog-results"]
        html = envelope.html_for_zone("catalog-results")
        assert html.startswith('<ul data-next-zone="catalog-results">')
        assert "All products" not in html

    def test_zone_request_narrows_to_the_search_term(self, client, catalog_db) -> None:
        envelope = envelope_of(
            client.get_zones("/catalog/?q=iPhone", "catalog-results")
        )
        html = envelope.html_for_zone("catalog-results")
        assert "iPhone 15" in html

    def test_zone_request_stamps_the_partial_vary_headers(
        self, client, catalog_db
    ) -> None:
        response = client.get_zones("/catalog/", "catalog-results")
        vary = response["Vary"]
        assert "X-Next-Zone" in vary
        assert "X-Next-Merge" in vary


class TestInfiniteScrollAppend:
    """Cover the revealed sentinel and the append merge it drives."""

    def test_first_page_renders_a_sentinel_link(self, client, catalog_db) -> None:
        body = client.get("/catalog/").content.decode()
        assert 'id="results-sentinel"' in body
        assert 'data-next-merge="append"' in body
        assert 'data-next-lazy="revealed"' in body
        assert "page=2" in body

    def test_append_merge_grows_the_zone_without_replacing_it(
        self, client, catalog_db
    ) -> None:
        response = client.get_zones(
            "/catalog/?page=2",
            "catalog-results",
            HTTP_X_NEXT_MERGE="append",
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["append"]
        assert envelope.zone_targets() == ["catalog-results"]

    def test_appended_rows_do_not_overlap_the_first_page(
        self, client, catalog_db
    ) -> None:
        first = client.get_zones("/catalog/", "catalog-results")
        second = client.get_zones(
            "/catalog/?page=2",
            "catalog-results",
            HTTP_X_NEXT_MERGE="append",
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

    def test_last_page_drops_the_sentinel(self, client, catalog_db) -> None:
        body = client.get("/catalog/?page=999").content.decode()
        assert "results-sentinel" not in body

    def test_changing_the_query_re_morphs_the_zone(self, client, catalog_db) -> None:
        envelope = envelope_of(client.get_zones("/catalog/?q=novel", "catalog-results"))
        assert envelope.op_verbs() == ["morph"]
