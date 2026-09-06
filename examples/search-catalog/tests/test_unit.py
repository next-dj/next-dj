from decimal import Decimal
from types import SimpleNamespace

import pytest
from catalog.demo import seed_demo
from catalog.forms import PRESETS, PresetFilterForm
from catalog.models import Category, Product
from catalog.providers import (
    Filters,
    PageProvider,
    PageRequest,
    _decimal_or_none,
    parse_filters,
)
from catalog.templatetags.catalog_qs import querystring
from catalog.zones import CATEGORY_ZONES, LISTING_ZONES, zone_target
from django.core.management import call_command


pytestmark = pytest.mark.django_db


class _PageParam:
    """Stand-in for the `page` parameter the provider is asked to resolve."""

    name = "page"
    annotation = None


def _resolve_page(rf, query: str) -> PageRequest:
    """Run `PageProvider` against a request carrying `query`."""
    request = rf.get("/" + (f"?{query}" if query else ""))
    return PageProvider().resolve(_PageParam(), SimpleNamespace(request=request))


class TestDemoData:
    """The demo catalog comes from a seed module rather than a migration."""

    @pytest.mark.django_db()
    def test_seed_command_creates_the_catalog(self) -> None:
        """`seed_demo` creates four categories and 25 products."""
        call_command("seed_demo")
        assert Category.objects.count() == 4
        assert Product.objects.count() == 25

    @pytest.mark.django_db()
    def test_seeding_twice_keeps_one_copy(self, demo_data) -> None:
        """A second run adds nothing, so the command stays safe to repeat."""
        seed_demo()
        assert Category.objects.count() == 4
        assert Product.objects.count() == 25


class TestModelStr:
    """Cover the human-readable representation of catalog models."""

    @pytest.mark.django_db()
    def test_category_str(self) -> None:
        """Return the category name from `__str__`."""
        cat = Category.objects.create(slug="x", name="Things")
        assert str(cat) == "Things"

    @pytest.mark.django_db()
    def test_product_str(self) -> None:
        """Return the product name and brand from `__str__`."""
        cat = Category.objects.create(slug="x", name="Things")
        product = Product.objects.create(
            category=cat, slug="p", name="Widget", brand="Acme", price=Decimal("1.00")
        )
        assert str(product) == "Widget (Acme)"


class TestFiltersDataclass:
    """Cover the `Filters.is_active` predicate and helpers."""

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            (Filters(), False),
            (Filters(q="iphone"), True),
            (Filters(brands=("Acme",)), True),
            (Filters(price_min=Decimal(1)), True),
            (Filters(price_max=Decimal(100)), True),
            (Filters(in_stock=True), True),
            (Filters(sort="price_asc"), True),
        ],
        ids=(
            "all_defaults",
            "search_term",
            "brand_selection",
            "price_floor",
            "price_ceiling",
            "in_stock_only",
            "non_default_sort",
        ),
    )
    def test_is_active(self, filters, expected) -> None:
        """Treat any non-default field as an active filter."""
        assert filters.is_active() is expected


class TestParseFilters:
    """Cover the parser used by both the provider and the context processor."""

    def test_empty_request(self, rf) -> None:
        """Return a default `Filters` instance when the query string is empty."""
        request = rf.get("/")
        assert parse_filters(request) == Filters()

    def test_brand_via_bracket_form(self, rf) -> None:
        """Fold `brand[]` entries into the brand tuple."""
        request = rf.get("/?brand[]=Acme&brand[]=Globex")
        assert parse_filters(request).brands == ("Acme", "Globex")

    def test_brand_via_comma_form(self, rf) -> None:
        """Split a comma-delimited brand value into individual brands."""
        request = rf.get("/?brand=Acme,Globex,")
        assert parse_filters(request).brands == ("Acme", "Globex")

    def test_decimal_or_none_invalid(self) -> None:
        """Return `None` when the input cannot be parsed as a decimal."""
        assert _decimal_or_none("abc") is None

    def test_decimal_or_none_blank(self) -> None:
        """Return `None` when the input is empty."""
        assert _decimal_or_none(None) is None


class TestPageProvider:
    """Cover the `PageProvider` value clamping and fallbacks."""

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("", PageRequest(number=1, per_page=6)),
            ("page=abc", PageRequest(number=1, per_page=6)),
            ("per_page=oops", PageRequest(number=1, per_page=6)),
            ("page=0", PageRequest(number=1, per_page=6)),
            ("per_page=999", PageRequest(number=1, per_page=60)),
            ("page=3&per_page=12", PageRequest(number=3, per_page=12)),
        ],
        ids=(
            "empty_query_string",
            "unparsable_page",
            "unparsable_per_page",
            "page_below_one",
            "per_page_above_max",
            "both_values_honoured",
        ),
    )
    def test_resolve_clamps_and_falls_back(self, rf, query, expected) -> None:
        """Clamp both paging values and fall back on anything unparsable."""
        assert _resolve_page(rf, query) == expected


class TestPresetFilterForm:
    """Cover the preset choices and the canonical URL each maps to."""

    def test_choices_match_the_preset_table(self) -> None:
        """Expose exactly the registered preset names as choices."""
        form = PresetFilterForm()
        names = [value for value, _ in form.fields["preset"].choices]
        assert names == list(PRESETS)

    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("preset", "expected"),
        [
            ("in_stock", "/catalog/?in_stock=1"),
            ("cheapest", "/catalog/?sort=price_asc"),
            ("newest", "/catalog/?sort=newest"),
        ],
        ids=("in_stock", "cheapest", "newest"),
    )
    def test_target_maps_preset_to_canonical_url(self, preset, expected) -> None:
        """Build the canonical listing URL for each preset."""
        form = PresetFilterForm(data={"preset": preset})
        assert form.is_valid()
        assert form._target() == expected

    def test_unknown_preset_fails_validation(self) -> None:
        """Reject a preset name that is not in the table."""
        form = PresetFilterForm(data={"preset": "nonsense"})
        assert not form.is_valid()


class TestQuerystringTemplateTag:
    """Cover the `querystring` template helper."""

    def test_querystring_without_request(self) -> None:
        """Encode only the overrides when the context lacks a request."""
        assert querystring({}, page=2) == "page=2"

    def test_querystring_drops_none_overrides(self, rf) -> None:
        """Skip overrides whose value is `None`."""
        request = rf.get("/?keep=1")
        result = querystring({"request": request}, page=None)
        assert result == "keep=1"

    def test_querystring_replaces_existing_key(self, rf) -> None:
        """Replace existing keys when an override of the same name is given."""
        request = rf.get("/?page=1&brand=Acme")
        result = querystring({"request": request}, page=2)
        assert "page=2" in result
        assert "brand=Acme" in result
        assert "page=1" not in result


class TestZoneSets:
    """Cover the zone names the listings publish to their filter form."""

    def test_listing_zones_join_into_a_partial_target(self) -> None:
        """Encode the all-products set as one comma-delimited target value."""
        assert zone_target(LISTING_ZONES) == (
            "catalog-results,catalog-more,catalog-count,catalog-pager,catalog-chips"
        )

    def test_category_zones_drop_the_append_sentinel(self) -> None:
        """Leave `catalog-more` out of the paginated category listing."""
        assert zone_target(CATEGORY_ZONES) == (
            "catalog-results,catalog-count,catalog-pager,catalog-chips"
        )

    def test_category_set_is_a_subset_of_the_listing_set(self) -> None:
        """Keep both listings on the same zone vocabulary."""
        assert set(CATEGORY_ZONES) < set(LISTING_ZONES)
