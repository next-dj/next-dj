"""Framework-level defaults for the `settings.NEXT_FRAMEWORK` mapping.

The values are deep-copied into the merged view on every reload, and nothing here
imports the rest of the framework, so the configuration layer has no dependencies.
"""

from __future__ import annotations

from typing import Any, Final


USER_SETTING: str = "NEXT_FRAMEWORK"

AUTO: Final = "auto"
"""The value of every setting whose mode the framework picks per request."""

DEFAULTS: dict[str, Any] = {
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "DIRS": [],
            "APP_DIRS": True,
            "PAGES_DIR": "pages",
            "OPTIONS": {"context_processors": []},
        }
    ],
    "URL_NAME_TEMPLATE": "page_{name}",
    "URL_RESOLVER": "next.urls.TrieURLResolver",
    "DEPENDENCY_RESOLVER": "next.deps.DependencyResolver",
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "_components",
        }
    ],
    "COMPONENT_TEMPLATE_LOADER": "next.components.CachedComponentTemplateLoader",
    "STATIC_BACKENDS": [{"BACKEND": "next.static.StaticFilesBackend", "OPTIONS": {}}],
    "STATIC_DISCOVERY_CACHE": True,
    "STATIC_VERSION": None,
    "FORM_ACTION_BACKENDS": [
        {"BACKEND": "next.forms.RegistryFormActionBackend", "OPTIONS": {}}
    ],
    "PARTIAL_BACKENDS": [
        {
            "BACKEND": "next.partial.JsonPartialProtocolBackend",
            "OPTIONS": {
                "VERSION": None,
                "PUSH_WIZARD_STEPS": False,
                "SSE": {"HEARTBEAT_SECONDS": 25, "RETRY_MS": 3000},
            },
        }
    ],
    "TEMPLATE_LOADERS": ["next.pages.loaders.DjxTemplateLoader"],
    "NEXT_JS_OPTIONS": {},
    "METADATA": {
        "RENDERER": "next.pages.HtmlMetadataRenderer",
        "DEFAULTS": {},
        "CANONICAL_QUERY": [],
    },
    "SITE": {"URL": None, "NAME": None, "INDEXABLE": AUTO},
    "SEO": {
        "SITEMAP_BACKENDS": [
            {"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": {}}
        ]
    },
    "CSRF_DELIVERY": AUTO,
    "CSP_NONCE": True,
    "CONSENT": {
        "BACKEND": "next.consent.CookieConsentBackend",
        "CATEGORIES": ["necessary"],
        "SERVER_RENDER": AUTO,
        "OPTIONS": {
            "cookie_name": "next_consent",
            "max_age": 15552000,
            "samesite": "Lax",
            "secure": None,
            "domain": None,
            "path": "/",
        },
    },
    "STRICT_CONTEXT": False,
    "STRICT_LOADING": False,
    "LAZY_COMPONENT_MODULES": False,
    "FORM_AUTODISCOVER": True,
    "FORM_ANCHOR_FILES": None,
    "JS_CONTEXT_SERIALIZER": None,
    "FORM_WIZARD_BACKEND": {
        "BACKEND": "next.forms.SessionFormWizardBackend",
        "OPTIONS": {},
    },
}
