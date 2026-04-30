import importlib
from decimal import Decimal

import pytest
from catalog.models import Category, Product
from catalog.providers import (
    Filters,
    PageProvider,
    PageRequest,
    _decimal_or_none,
    parse_filters,
)
from catalog.templatetags.catalog_qs import querystring
from django.apps import apps as django_apps
from django.test import RequestFactory


_SEED_MIGRATION = importlib.import_module(
    "catalog.migrations.0002_seed_catalog",
)


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a request factory used to build query-string requests."""
    return RequestFactory()


class TestDemoData:
    """Confirm the pre-loaded demo catalog is present out of the box."""

    @pytest.mark.django_db()
    def test_demo_catalog_is_pre_loaded(self) -> None:
        """The data migration creates four categories and 25 products."""
        assert Category.objects.count() == 4
        assert Product.objects.count() == 25

    @pytest.mark.django_db()
    def test_reverse_migration_drops_every_row(self) -> None:
        """Reversing the data migration removes both products and categories."""
        _SEED_MIGRATION.unseed(django_apps, None)
        assert Product.objects.count() == 0
        assert Category.objects.count() == 0


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
            category=cat,
            slug="p",
            name="Widget",
            brand="Acme",
            price=Decimal("1.00"),
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

    def _build_request(self, rf, query: str = ""):
        return rf.get("/" + ("?" + query if query else ""))

    def test_page_request_has_defaults(self, rf) -> None:
        """Default to page 1 with the configured page size."""
        request = self._build_request(rf)
        ctx = type("ctx", (), {"request": request})()
        provider = PageProvider()

        class Param:
            name = "page"
            annotation = None

        assert provider.resolve(Param(), ctx) == PageRequest(number=1, per_page=6)

    def test_invalid_page_falls_back_to_one(self, rf) -> None:
        """Treat an unparsable page parameter as page 1."""
        request = self._build_request(rf, "page=abc")
        ctx = type("ctx", (), {"request": request})()
        provider = PageProvider()

        class Param:
            name = "page"
            annotation = None

        assert provider.resolve(Param(), ctx).number == 1

    def test_invalid_per_page_falls_back_to_default(self, rf) -> None:
        """Treat an unparsable per_page parameter as the default page size."""
        request = self._build_request(rf, "per_page=oops")
        ctx = type("ctx", (), {"request": request})()
        provider = PageProvider()

        class Param:
            name = "page"
            annotation = None

        assert provider.resolve(Param(), ctx).per_page == 6


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
