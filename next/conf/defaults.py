"""Framework-level defaults for the `settings.NEXT_FRAMEWORK` mapping.

The values stored here are deep-copied into the merged view on every
reload. Nothing in this module imports from the rest of the framework,
which keeps the configuration layer at the bottom of the dependency
graph.
"""

from __future__ import annotations

from typing import Any


USER_SETTING: str = "NEXT_FRAMEWORK"

DEFAULTS: dict[str, Any] = {
    "DEFAULT_PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "DIRS": [],
            "APP_DIRS": True,
            "PAGES_DIR": "pages",
            "OPTIONS": {
                "context_processors": [],
            },
        },
    ],
    "URL_NAME_TEMPLATE": "page_{name}",
    "DEFAULT_COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "_components",
        },
    ],
    "DEFAULT_STATIC_BACKENDS": [
        {
            "BACKEND": "next.static.StaticFilesBackend",
            "OPTIONS": {},
        },
    ],
    "NEXT_JS_OPTIONS": {},
    "STRICT_CONTEXT": False,
    "LAZY_COMPONENT_MODULES": False,
}
