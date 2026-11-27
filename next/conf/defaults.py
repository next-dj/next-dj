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
                "BACKEND": "next.urls.FilePageBackend",
                "DIRS": [],
                "APP_DIRS": True,
                "PAGES_DIR": "pages",
                "OPTIONS": {"context_processors": []},
            }
        ],
        "URL_NAME_TEMPLATE": "page_{name}",
        "URL_RESOLVER": "next.urls.TrieURLResolver",
        "DEPENDENCY_RESOLVER": "next.deps.DependencyResolver",
        "LOADER": {"WINDOW": 0.001, "MAX_BATCH": 500},
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
            {
                "BACKEND": "next.forms.RegistryFormActionBackend",
                "OPTIONS": {"SHAPER": "next.partial.FormShaper"},
            }
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
            "TICKET_BINDING": "next.realtime.SessionBinding",
            "WS_PATH": "/_next/ws/",
            "HEARTBEAT": 25.0,
            "GATEWAY_TOKEN": None,
            "REPLAY": {"SIZE": 100, "TTL": 300},
            "PRESENCE": {"WINDOW": 1.0, "SIZE": 100, "MEMBER_BYTES": 512},
            "HANDLERS": None,
            "UPSTREAM": {
                "RATE": 10.0,
                "BURST": 20,
                "MAX_BYTES": 16384,
                "CONCURRENCY": 16,
                "TIMEOUT": 10.0,
            },
            "TUNNEL": {"CONCURRENCY": 4, "TIMEOUT": 30.0, "MAX_REPLY_BYTES": 1048576},
        },
        "DEV_SERVER": "next.server.asgi.UvicornServer",
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
        "TRANSACTIONS": {
            "SAFE": AUTO,
            "UNSAFE": AUTO,
            "ALIASES": ("default",),
            "RULE": None,
        },
        "BOUNDARIES": {
            "LOADING_TEMPLATE": None,
            "ERROR_TEMPLATE": None,
            "RAISE": False,
            "REPORTER": None,
            "ATOMIC": "writes",
            "KINDS": "detailed",
            "DEBUG_DETAIL": True,
        },
        "DEFER": {
            "EXECUTOR": {
                "BACKEND": "next.defer.LaneExecutor",
                "OPTIONS": {
                    "MAX_WORKERS": 8,
                    "MAX_QUEUE": 64,
                    "CONN_IDLE": 60.0,
                    "SHUTDOWN_TIMEOUT": 5.0,
                },
                "PER_REQUEST": 4,
                "QUEUE_TIMEOUT": 0.05,
                "SHED_DELAY": 1.0,
                "MAX_REQUEST_JOBS": 16,
                "PARALLEL": True,
                "SQLITE": "request",
                "THREAD_LOCAL_MIDDLEWARE": (),
            },
            "DELIVERY": {
                "BACKEND": "next.defer.DefaultDeliveryPolicy",
                "OPTIONS": {"STREAM": True, "BOTS": None, "SYNC_WORKERS": "inline"},
            },
            "SERVER": {
                "BACKEND": "next.defer.DefaultServerProbe",
                "OPTIONS": {
                    "INCREMENTAL": None,
                    "CONCURRENT": None,
                    "BUFFERED_MARKS": None,
                },
            },
            "TOKENS": {
                "BACKEND": "next.defer.StackedTokenBackend",
                "OPTIONS": {
                    "STORAGE": "next.defer.InlineStorage",
                    "SEAL": "next.defer.SignedSeal",
                    "BINDING": "auto",
                    "CARRIER": "header",
                    "MAX_AGE": 600,
                    "MAX_BYTES": 2048,
                    "MAX_STORED_BYTES": 65536,
                    "CACHE_ALIAS": "default",
                    "EPOCH": 1,
                },
            },
            "CONNECTION_STATE": [],
            "THREAD_STATE": [],
            "ADMISSION": {"BACKEND": "next.defer.QueueDelay", "OPTIONS": {}},
            "DEADLINE": 10.0,
            "DEADLINE_TO_DB": "always",
            "WRITE_GRACE": 2.0,
            "WRITE_TIMEOUT": 10.0,
            "REQUEST_CONCURRENCY": 1,
            "MAX_BOUNDARIES": 64,
            "MAX_PER_SITE": 4,
            "MAX_DEPTH": 4,
            "MAX_INSTANCES": 1000,
            "MAX_STREAMS": "auto",
            "NOSCRIPT": True,
        },
        "COMPRESSION": {
            "ENABLED": True,
            "BACKENDS": [
                {
                    "BACKEND": "next.compression.ZstdBackend",
                    "OPTIONS": {"LEVEL": 3, "WINDOW": 17},
                },
                {
                    "BACKEND": "next.compression.BrotliBackend",
                    "OPTIONS": {"LEVEL": 4, "WINDOW": 18},
                },
                {"BACKEND": "next.compression.GzipBackend", "OPTIONS": {"LEVEL": 4}},
            ],
            "ORDER": ("zstd", "br", "gzip"),
            "MIN_SIZE": 1024,
            "CONTENT_TYPES": (
                "text/*",
                "application/json",
                "application/*+json",
                "application/x-ndjson",
                "application/javascript",
                "application/xml",
                "application/*+xml",
                "image/svg+xml",
            ),
            "STREAMS": "self",
            "SSE": True,
            "SSE_TOPICS": "off",
            "PADDING": 32,
            "COALESCE": 0.002,
            "RESPECT_NO_TRANSFORM": True,
            "EXCLUDE": None,
        },
        "ROUTER": {
            "ENABLED": True,
            "SCOPE": "pages",
            "EXCLUDE": (),
            "FOREIGN": "hard",
            "PREFETCH": "intent",
            "PREFETCH_DELAY": 0.065,
            "PREFETCH_TTL": 30.0,
            "PREFETCH_SHELL_TTL": 300.0,
            "PREFETCH_ENTRIES": 16,
            "PRESS": "mouse",
            "SNAPSHOTS": 10,
            "PROGRESS_DELAY": 0.15,
            "PROGRESS": True,
            "TRANSITIONS": False,
            "INTERCEPT": "opt_in",
            "SPECULATION": None,
            "CUT": "next.router.VerifiedCut",
            "DIGEST": "next.router.ChromeDigest",
            "SHED": "next.router.LoadShed",
            "ACTIVE_LINKS": {
                "ENABLED": True,
                "PASS": "template",
                "CONTAINERS": ("nav", "[role=navigation]", "[data-next-nav]"),
            },
        },
    }
)
"""Every setting the framework declares, read-only so no import can add or swap one."""
