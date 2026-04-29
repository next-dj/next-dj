from typing import ClassVar

from django.db import models


class Category(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering: ClassVar = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="products",
    )
    slug = models.SlugField(max_length=80)
    name = models.CharField(max_length=200)
    brand = models.CharField(max_length=64, db_index=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    in_stock = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["category", "slug"],
                name="uniq_product_per_cat",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["category", "in_stock"]),
            models.Index(fields=["price"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.brand})"
