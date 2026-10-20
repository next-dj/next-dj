from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Self

from django.db import models
from django.urls import reverse


_SORT_ORDERINGS = {
    "price_asc": ("price",),
    "price_desc": ("-price",),
    "name": ("name",),
    "newest": ("-created_at",),
}


@dataclass(frozen=True, slots=True)
class Filters:
    """Search criteria that narrow and order a catalog listing."""

    q: str = ""
    brands: tuple[str, ...] = ()
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    in_stock: bool = False
    sort: str = "newest"

    def is_active(self) -> bool:
        """Return True when at least one filter is non-default."""
        return bool(
            self.q
            or self.brands
            or self.price_min is not None
            or self.price_max is not None
            or self.in_stock
            or self.sort != "newest"
        )


class ProductQuerySet(models.QuerySet):
    def brand_list(self) -> list[str]:
        """Return a sorted, distinct list of brand names in this queryset."""
        return list(self.order_by("brand").values_list("brand", flat=True).distinct())

    def matching(self, filters: Filters) -> Self:
        """Narrow to the products a filter set selects, in the order it asks for."""
        qs = self.select_related("category")
        if filters.q:
            qs = qs.filter(name__icontains=filters.q)
        if filters.brands:
            qs = qs.filter(brand__in=filters.brands)
        if filters.price_min is not None:
            qs = qs.filter(price__gte=filters.price_min)
        if filters.price_max is not None:
            qs = qs.filter(price__lte=filters.price_max)
        if filters.in_stock:
            qs = qs.filter(in_stock=True)
        ordering = _SORT_ORDERINGS.get(filters.sort, _SORT_ORDERINGS["newest"])
        return qs.order_by(*ordering)


class Category(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    tagline = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering: ClassVar = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="products"
    )
    slug = models.SlugField(max_length=80)
    name = models.CharField(max_length=200)
    brand = models.CharField(max_length=64, db_index=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    in_stock = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-created_at"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["category", "slug"], name="uniq_product_per_cat"
            )
        ]
        # Django's index-name cap moved, so an unnamed index drifts in `makemigrations`.
        indexes: ClassVar = [
            models.Index(fields=["category", "in_stock"], name="product_cat_stock_idx"),
            models.Index(fields=["price"], name="product_price_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.brand})"

    def get_absolute_url(self) -> str:
        """Return the detail page, addressed through the category of the product."""
        return reverse(
            "next:page_catalog_category_slug",
            kwargs={"category": self.category.slug, "slug": self.slug},
        )


class Lead(models.Model):
    """A visitor who left an email on the landing of a category."""

    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="leads"
    )
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email} ({self.category})"
