from decimal import Decimal

from django.db import migrations


CATEGORIES = [
    ("electronics", "Electronics"),
    ("books", "Books"),
    ("home", "Home"),
    ("clothing", "Clothing"),
]

BRANDS = ["Acme", "Globex", "Initech", "Hooli"]


def seed(apps, schema_editor) -> None:
    """Populate the catalog with deterministic demo data."""
    Category = apps.get_model("catalog", "Category")
    Product = apps.get_model("catalog", "Product")
    cats = [
        Category.objects.create(slug=slug, name=name) for slug, name in CATEGORIES
    ]
    for index in range(24):
        category = cats[index % len(cats)]
        Product.objects.create(
            category=category,
            slug=f"item-{index:02d}",
            name=f"Item {index:02d}",
            brand=BRANDS[index % len(BRANDS)],
            description=f"Demo product number {index:02d}.",
            price=Decimal("9.99") + index * Decimal("17.50"),
            in_stock=index % 6 != 0,
        )
    Product.objects.create(
        category=cats[0],
        slug="iphone-15",
        name="iPhone 15",
        brand="Acme",
        description="Flagship handset used by routing tests.",
        price=Decimal("999.00"),
        in_stock=True,
    )


def unseed(apps, schema_editor) -> None:
    """Drop seeded data when the migration is reversed."""
    apps.get_model("catalog", "Product").objects.all().delete()
    apps.get_model("catalog", "Category").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
