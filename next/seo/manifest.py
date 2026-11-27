"""The startup wiring of the SEO routes and the port the file router reads."""

from next.manifest import Bind, Manifest
from next.ports import seo_routes_slot
from next.urls.manager import seo_routes_version

from . import checks, signals
from .ports import SeoRoutesImpl


manifest = Manifest(
    "seo",
    ports=(Bind(seo_routes_slot, SeoRoutesImpl()),),
    startup=(seo_routes_version.move,),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
