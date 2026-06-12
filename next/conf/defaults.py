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
    "PAGE_BACKENDS": [
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
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "_components",
        },
    ],
    "STATIC_BACKENDS": [
        {
            "BACKEND": "next.static.StaticFilesBackend",
            "OPTIONS": {},
        },
    ],
    "FORM_ACTION_BACKENDS": [
        {
            "BACKEND": "next.forms.RegistryFormActionBackend",
            "OPTIONS": {},
        },
    ],
    "TEMPLATE_LOADERS": [
        "next.pages.loaders.DjxTemplateLoader",
    ],
    "NEXT_JS_OPTIONS": {},
    "STRICT_CONTEXT": False,
    "LAZY_COMPONENT_MODULES": False,
    "FORM_AUTODISCOVER": True,
    "FORM_ANCHOR_FILES": None,
    "JS_CONTEXT_SERIALIZER": None,
    "FORM_WIZARD_BACKEND": {
        "BACKEND": "next.forms.wizard.SessionFormWizardBackend",
        "OPTIONS": {},
    },
}
