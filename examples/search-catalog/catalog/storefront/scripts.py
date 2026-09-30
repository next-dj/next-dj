from django.conf import settings

from next.scripts import Script, Strategy


scripts = (
    Script(
        "google-analytics",
        src="catalog/tags/ga4.js",
        strategy=Strategy.DEFER,
        category="analytics",
        attrs={"data-measurement-id": settings.GA_MEASUREMENT_ID},
    ),
    Script(
        "meta-pixel",
        src="catalog/tags/meta-pixel.js",
        strategy=Strategy.DEFER,
        category="marketing",
        auto=False,
        attrs={"data-pixel-id": settings.META_PIXEL_ID},
    ),
)
