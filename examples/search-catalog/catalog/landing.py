from .models import Category, Product


LANDING_OFFERS = 3
LEAD_ZONE = "lead-form"

FAQ = (
    (
        "How fast is delivery?",
        "Orders placed before 2 pm ship the same day and most arrive in two days.",
    ),
    (
        "Can I return an item?",
        "Every item can go back within 30 days, and the return label is on us.",
    ),
    (
        "Is there a warranty?",
        "Everything carries the warranty of its maker plus one more year from us.",
    ),
)


def landing_offers(category: Category) -> list[Product]:
    """Return the cheapest in-stock products of a category, the ones a landing sells."""
    return list(
        category.products.filter(in_stock=True)
        .select_related("category")
        .order_by("price")[:LANDING_OFFERS]
    )


def faq_node() -> dict[str, object]:
    """Return the questions every landing answers as one schema.org `FAQPage`."""
    return {
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
