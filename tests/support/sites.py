import sys
import types
from collections.abc import Callable

from django.conf.urls.i18n import i18n_patterns
from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from django.urls import URLPattern, URLResolver, include, path


type Patterns = list[URLPattern | URLResolver]

BASE = "https://acme.example"
WITH_BASE = {"SITE": {"URL": BASE}}
CLOSED_SITE = {"SITE": {"INDEXABLE": False}}
LANGUAGES = [("en", "English"), ("de", "German")]
I18N = {"LANGUAGES": LANGUAGES, "LANGUAGE_CODE": "en", "USE_I18N": True}
NAMESPACED_URLCONF = "tests.urls.urls_namespaced"


def site_settings(**site: object) -> override_settings:
    """Return an override whose `NEXT_FRAMEWORK` carries `site` as its `SITE`."""
    return override_settings(NEXT_FRAMEWORK={"SITE": site})


def mine(request: HttpRequest) -> HttpResponse:
    """Answer an address the project serves itself."""
    return HttpResponse("mine")


def _urlconf(name: str, build: Callable[[], Patterns]) -> str:
    """Register a URLconf module under `name` and return the dotted path it takes.

    `i18n_patterns()` reads `USE_I18N` when called, so the patterns build on first read.
    """
    dotted = f"{__name__}.{name}"
    module = types.ModuleType(dotted)

    def urlpatterns(attr: str) -> Patterns:
        if attr != "urlpatterns":
            raise AttributeError(attr)
        patterns = build()
        module.urlpatterns = patterns
        return patterns

    module.__getattr__ = urlpatterns
    sys.modules[dotted] = module
    return dotted


def _pages(*, prefix_default_language: bool) -> Patterns:
    return i18n_patterns(
        path("", include("next.urls")), prefix_default_language=prefix_default_language
    )


def _seo() -> URLResolver:
    return path("", include("next.seo.urls"))


def _own() -> Patterns:
    return [path("sitemap.xml", mine), path("robots.txt", mine)]


I18N_URLCONF = _urlconf("i18n", lambda: _pages(prefix_default_language=False))
I18N_PREFIXED_URLCONF = _urlconf(
    "i18n_prefixed", lambda: _pages(prefix_default_language=True)
)
I18N_ROUTED = {**I18N, "ROOT_URLCONF": I18N_URLCONF}
PREFIX_ONLY_URLCONF = _urlconf(
    "prefix_only", lambda: [path("prefix/", include("next.urls"))]
)
PREFIXED_URLCONF = _urlconf(
    "prefixed", lambda: [_seo(), path("prefix/", include("next.urls"))]
)
USER_URLCONF = _urlconf("user", lambda: [path("", include("next.urls")), *_own()])
FEED_URLCONF = _urlconf("feed", lambda: [path("feed/", mine, name="feed")])
SHADOWED_URLCONF = _urlconf(
    "shadowed", lambda: [*_own(), path("", include("next.urls"))]
)
SEO_I18N_URLCONF = _urlconf(
    "seo_i18n", lambda: [_seo(), *_pages(prefix_default_language=False)]
)
SEO_I18N_AFTER_URLCONF = _urlconf(
    "seo_i18n_after", lambda: [*_pages(prefix_default_language=False), _seo()]
)
SEO_I18N_PREFIXED_URLCONF = _urlconf(
    "seo_i18n_prefixed", lambda: [_seo(), *_pages(prefix_default_language=True)]
)
