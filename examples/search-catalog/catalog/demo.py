from decimal import Decimal

from catalog.models import Category, Product


CATEGORIES = [
    ("electronics", "Electronics"),
    ("books", "Books"),
    ("home", "Home"),
    ("clothing", "Clothing"),
]

BRANDS = ["Acme", "Globex", "Initech", "Hooli"]


def seed_demo() -> None:
    """Populate the catalog with deterministic demo data."""
    cats = [
        Category.objects.get_or_create(slug=slug, defaults={"name": name})[0]
        for slug, name in CATEGORIES
    ]
    for index in range(24):
        category = cats[index % len(cats)]
        Product.objects.get_or_create(
            category=category,
            slug=f"item-{index:02d}",
            defaults={
                "name": f"Item {index:02d}",
                "brand": BRANDS[index % len(BRANDS)],
                "description": f"Demo product number {index:02d}.",
                "price": Decimal("9.99") + index * Decimal("17.50"),
                "in_stock": index % 6 != 0,
            },
        )
    Product.objects.get_or_create(
        category=cats[0],
        slug="iphone-15",
        defaults={
            "name": "iPhone 15",
            "brand": "Acme",
            "description": "Flagship handset used by routing tests.",
            "price": Decimal("999.00"),
            "in_stock": True,
        },
    )
