"""System checks for the `NEXT_FRAMEWORK` keys the forms subsystem reads.

The ids are `next.E044`, `next.E058`, `next.E059`, `next.E068` and `next.E045` for
`FORM_ACTION_BACKENDS`, `next.E051` and `next.E069` to `next.E071` for
`FORM_WIZARD_BACKEND`, and `next.E052` with `next.E086` for `FORM_ANCHOR_FILES`.
"""

from django.conf import settings
from django.core.checks import CheckMessage, Error, register

from next.checks import NEXT
from next.conf import import_class_cached
from next.forms.backends import FormActionBackend
from next.forms.wizard import FormWizardBackend


_FORM_ACTION_BACKEND_SETTINGS_KEY = "FORM_ACTION_BACKENDS"
_FORM_WIZARD_BACKEND_SETTINGS_KEY = "FORM_WIZARD_BACKEND"
_FORM_ANCHOR_FILES_SETTINGS_KEY = "FORM_ANCHOR_FILES"


@register(NEXT)
def check_form_action_backends_configuration(*args, **kwargs) -> list[CheckMessage]:
    """Validate `FORM_ACTION_BACKENDS` shape and import paths."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    configs = raw.get(_FORM_ACTION_BACKEND_SETTINGS_KEY)
    if configs is None:
        return []
    if not isinstance(configs, list):
        key = _FORM_ACTION_BACKEND_SETTINGS_KEY
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must be a list.", obj=settings, id="next.E044"
            )
        ]
    errors: list[CheckMessage] = []
    for index, config in enumerate(configs):
        prefix = f"NEXT_FRAMEWORK['{_FORM_ACTION_BACKEND_SETTINGS_KEY}'][{index}]"
        errors.extend(_validate_single_form_action_backend(config, prefix))
    return errors


def _validate_single_form_action_backend(
    config: object, prefix: str
) -> list[CheckMessage]:
    """Validate one `FORM_ACTION_BACKENDS` entry down to its backend class."""
    if not isinstance(config, dict):
        return [Error(f"{prefix} must be a dict.", obj=settings, id="next.E058")]
    backend_path = config.get("BACKEND")
    if not isinstance(backend_path, str):
        return [
            Error(f"{prefix}.BACKEND must be a string.", obj=settings, id="next.E059")
        ]
    try:
        cls = import_class_cached(backend_path)
    except ImportError as exc:
        return [
            Error(
                f"{prefix}.BACKEND {backend_path!r} cannot be imported: {exc}.",
                obj=settings,
                id="next.E068",
            )
        ]
    if not isinstance(cls, type) or not issubclass(cls, FormActionBackend):
        return [
            Error(
                f"{prefix}.BACKEND {backend_path!r} must subclass FormActionBackend.",
                obj=settings,
                id="next.E045",
            )
        ]
    return []


@register(NEXT)
def check_form_wizard_backend(*args, **kwargs) -> list[CheckMessage]:
    """Validate `FORM_WIZARD_BACKEND` shape and import path."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    config = raw.get(_FORM_WIZARD_BACKEND_SETTINGS_KEY)
    if config is None:
        return []
    return _validate_form_wizard_backend(config)


def _validate_form_wizard_backend(config: object) -> list[CheckMessage]:
    """Validate the `FORM_WIZARD_BACKEND` entry down to its backend class."""
    key = _FORM_WIZARD_BACKEND_SETTINGS_KEY
    if not isinstance(config, dict):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must be a dict with a BACKEND key.",
                obj=settings,
                id="next.E051",
            )
        ]
    backend_path = config.get("BACKEND")
    if not isinstance(backend_path, str):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}].BACKEND must be a string.",
                obj=settings,
                id="next.E069",
            )
        ]
    try:
        cls = import_class_cached(backend_path)
    except ImportError as exc:
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}].BACKEND {backend_path!r} cannot be "
                f"imported: {exc}.",
                obj=settings,
                id="next.E070",
            )
        ]
    if not (isinstance(cls, type) and issubclass(cls, FormWizardBackend)):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}].BACKEND {backend_path!r} must subclass "
                "FormWizardBackend.",
                obj=settings,
                id="next.E071",
            )
        ]
    return []


@register(NEXT)
def check_form_anchor_files(*args, **kwargs) -> list[CheckMessage]:
    """Validate that FORM_ANCHOR_FILES is None or a collection of strings."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    value = raw.get(_FORM_ANCHOR_FILES_SETTINGS_KEY)
    if value is None:
        return []
    key = _FORM_ANCHOR_FILES_SETTINGS_KEY
    # Only a list round-trips through the settings merge, so tuples and sets
    # are rejected here instead of silently falling back to the defaults.
    if not isinstance(value, list):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must be None or a list of strings.",
                obj=settings,
                id="next.E052",
            )
        ]
    if not all(isinstance(item, str) for item in value):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must contain only strings.",
                obj=settings,
                id="next.E086",
            )
        ]
    return []


__all__ = [
    "check_form_action_backends_configuration",
    "check_form_anchor_files",
    "check_form_wizard_backend",
]
