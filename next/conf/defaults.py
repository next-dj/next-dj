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
            "SAFE": False,
            "UNSAFE": True,
            "ALIASES": ("default",),
            "RULE": None,
        },
        "DEFER": {
            "EXECUTOR": {
                "BACKEND": "next.suspense.LaneExecutor",
                "OPTIONS": {
                    "MAX_WORKERS": 16,
                    "PER_REQUEST": 4,
                    "QUEUE_TIMEOUT": 0.05,
                    "MAX_QUEUE": 64,
                    "MAX_FOREGROUND": 16,
                    "CONN_IDLE": 60.0,
                    "PARALLEL": True,
                    "SQLITE": "foreground",
                    "THREAD_LOCAL_MIDDLEWARE": (),
                    "PLACEMENT": {
                        "MODE": "adaptive",
                        "HANDOFF": 0.0003,
                        "CPU_RATIO": 0.8,
                        "CPU_MAX": 0.002,
                        "EXPLORE": 3,
                    },
                },
            },
            "POLICY": {
                "BACKEND": "next.suspense.DefaultStreamPolicy",
                "OPTIONS": {"STREAM": True, "BOTS": None, "SYNC_WORKERS": "inline"},
            },
            "TRANSPORT": {
                "BACKEND": "next.suspense.DefaultStreamTransport",
                "OPTIONS": {
                    "INCREMENTAL": None,
                    "CONCURRENT": None,
                    "BUFFERED_MARKS": None,
                },
            },
            "TOKENS": {
                "BACKEND": "next.suspense.StackedTokenBackend",
                "OPTIONS": {
                    "CODEC": "next.suspense.JsonCodec",
                    "STORAGE": "next.suspense.InlineStorage",
                    "SEAL": "next.suspense.SignedSeal",
                    "BINDING": "next.suspense.NoBinding",
                    "TRANSPORT": "next.suspense.HeaderTransport",
                    "MAX_AGE": 600,
                    "BUCKET": None,
                    "MAX_BYTES": 2048,
                    "MAX_STORED_BYTES": 65536,
                    "CACHE_ALIAS": "default",
                    "KEYS": None,
                    "EPOCH": 1,
                },
            },
            "CONNECTION_STATE": [],
            "THREAD_STATE": [],
            "ADMISSION": "next.suspense.QueueDelay",
            "DEADLINE": 10.0,
            "DEADLINE_TO_DB": "state",
            "DB_TIMEOUT_FLOOR": 0.05,
            "WRITE_GRACE": 2.0,
            "REQUEST_CONCURRENCY": 1,
            "MAX_BOUNDARIES": 64,
            "MAX_PER_SITE": 4,
            "MAX_DEPTH": 4,
            "MAX_INSTANCES": 1000,
            "MAX_STREAMS": "auto",
            "BATCH_WINDOW": 0.001,
            "LOADER_MAX_BATCH": 500,
            "LOADING": None,
            "ERROR": None,
            "NOSCRIPT": True,
            "ERRORS": {
                "RAISE": False,
                "REPORTER": None,
                "ATOMIC": "writes",
                "KINDS": "detailed",
                "DEBUG_DETAIL": True,
            },
            "MORE": {"MAX_RESTORE": 10},
        },
        "COMPRESSION": {
            "ENABLED": True,
            "BACKENDS": [
                "next.compression.ZstdBackend",
                "next.compression.BrotliBackend",
                "next.compression.GzipBackend",
            ],
            "ORDER": ("zstd", "br", "gzip"),
            "LEVELS": {"zstd": 3, "br": 4, "gzip": 4},
            "WINDOWS": {"zstd": 17, "br": 18},
            "MIN_SIZE": 1024,
            "CONTENT_TYPES": (
                "text/*",
                "application/json",
                "application/ld+json",
                "application/manifest+json",
                "application/problem+json",
                "application/x-ndjson",
                "application/javascript",
                "application/xml",
                "application/xhtml+xml",
                "application/rss+xml",
                "application/atom+xml",
                "image/svg+xml",
            ),
            "STREAMS": "self",
            "SSE": True,
            "SSE_TOPICS": "off",
            "PADDING": 32,
            "COALESCE_MS": 2,
            "OFFLOAD_BYTES": 16384,
            "RESPECT_NO_TRANSFORM": True,
            "EXCLUDE": None,
        },
        "ROUTER": {
            "ENABLED": True,
            "SCOPE": "all",
            "EXCLUDE": (),
            "FOREIGN": "hard",
            "PREFETCH": "intent",
            "PREFETCH_DELAY": 0.065,
            "PREFETCH_TTL": 30.0,
            "PREFETCH_SHELL_TTL": 300.0,
            "PREFETCH_ENTRIES": 16,
            "SNAPSHOTS": 10,
            "PROGRESS_DELAY": 0.15,
            "PROGRESS": True,
            "TRANSITIONS": False,
            "INTERCEPT": "opt-in",
            "SPECULATION": None,
            "MAX_LAYOUTS": 32,
            "CUT": "next.router.VerifiedCut",
            "DIGEST": "next.router.ChromeDigest",
            "CURRENT": {
                "ENABLED": True,
                "BACKEND": "next.router.CurrentLinks",
                "CONTAINERS": ("nav", "[role=navigation]", "[data-next-nav]"),
            },
        },
    }
)
"""Every setting the framework declares, read-only so no import can add or swap one."""
