LISTING_ZONES = (
    "catalog-results",
    "catalog-more",
    "catalog-count",
    "catalog-pager",
    "catalog-chips",
)

CATEGORY_ZONES = ("catalog-results", "catalog-count", "catalog-pager", "catalog-chips")


def zone_target(zones: tuple[str, ...]) -> str:
    """Join zone names into the comma-delimited value a partial target carries."""
    return ",".join(zones)
