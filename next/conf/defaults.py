"""Framework-level defaults for the `settings.NEXT_FRAMEWORK` mapping.

Only the top level is read-only, so a nested value must never be mutated, and the
runtime reads the deep-frozen `FROZEN_DEFAULTS`. Nothing here imports the framework.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final


if TYPE_CHECKING:
    from collections.abc import Mapping


USER_SETTING: str = "NEXT_FRAMEWORK"

AUTO: Final = "auto"
"""The value of every setting whose mode the framework picks per request."""

DEFAULTS: Final[Mapping[str, Any]] = MappingProxyType(
    {
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
        "STATIC_BACKENDS": [
            {"BACKEND": "next.static.StaticFilesBackend", "OPTIONS": {}}
        ],
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
        "REALTIME": {
            "BROKER": {
                "BACKEND": "next.realtime.MemoryBroker",
                "OPTIONS": {"QUEUE_SIZE": 256},
            },
            "TRANSPORTS": None,
            "TICKET_MAX_AGE": 60,
            "WS_PATH": "/_next/ws/",
            "HEARTBEAT": 25.0,
            "GATEWAY_TOKEN": None,
            "REPLAY": {"SIZE": 100, "TTL": 300},
            "PRESENCE": {"WINDOW": 1.0, "SIZE": 100, "MEMBER_BYTES": 512},
        },
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
        "LOGIN_REQUIRED": False,
        "LAZY_COMPONENT_MODULES": False,
        "FORM_AUTODISCOVER": True,
        "FORM_ANCHOR_FILES": None,
        "JS_CONTEXT_SERIALIZER": None,
        "FORM_WIZARD_BACKEND": {
            "BACKEND": "next.forms.SessionFormWizardBackend",
            "OPTIONS": {},
        },
    }
)
"""Every setting the framework declares, read-only so no import can add or swap one."""
