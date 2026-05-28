"""System checks for the forms subsystem."""

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.conf import import_class_cached

from .backends import (
    FormActionBackend,
    _action_collisions,
    _handler_fingerprint,
    clear_action_collisions,
    record_possible_collision,
)
from .base import _invalid_meta_scope_classes, _outside_base_dir_classes
from .decorators import _action_applied_to_class


_FORM_ACTION_BACKEND_SETTINGS_KEY = "DEFAULT_FORM_ACTION_BACKENDS"


@register(Tags.compatibility)
def check_form_action_collisions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Flag two `@action` calls that share a name but come from different handlers.

    Re-registration of the same handler (for example during autoreload) is
    safe and does not trigger the check.
    """
    return [
        Error(
            f"Form action {name!r} is registered by {len(fps)} different "
            "handlers. Rename one of them or change the namespace to avoid "
            "the collision.",
            obj=settings,
            id="next.E041",
        )
        for name, fps in _action_collisions.items()
    ]


@register(Tags.compatibility)
def check_form_action_backends_configuration(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate `DEFAULT_FORM_ACTION_BACKENDS` shape and import paths."""
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
                f"NEXT_FRAMEWORK[{key!r}] must be a list.",
                obj=settings,
                id="next.E044",
            ),
        ]
    errors: list[CheckMessage] = []
    for index, config in enumerate(configs):
        prefix = f"NEXT_FRAMEWORK['{_FORM_ACTION_BACKEND_SETTINGS_KEY}'][{index}]"
        errors.extend(_validate_single_form_action_backend(config, prefix))
    return errors


def _validate_single_form_action_backend(
    config: object,
    prefix: str,
) -> list[CheckMessage]:
    if not isinstance(config, dict):
        return [Error(f"{prefix} must be a dict.", obj=settings, id="next.E044")]
    backend_path = config.get("BACKEND")
    if not isinstance(backend_path, str):
        return [
            Error(
                f"{prefix}.BACKEND must be a string.",
                obj=settings,
                id="next.E044",
            ),
        ]
    try:
        cls = import_class_cached(backend_path)
    except ImportError as exc:
        return [
            Error(
                f"{prefix}.BACKEND {backend_path!r} cannot be imported: {exc}.",
                obj=settings,
                id="next.E044",
            ),
        ]
    if not isinstance(cls, type) or not issubclass(cls, FormActionBackend):
        return [
            Error(
                f"{prefix}.BACKEND {backend_path!r} must subclass FormActionBackend.",
                obj=settings,
                id="next.E045",
            ),
        ]
    return []


@register(Tags.compatibility)
def check_forms_outside_base_dir(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when form classes are declared outside BASE_DIR."""
    return [
        DjangoWarning(
            f"Form class {cls_name!r} declared in {file_path!r} which is outside "
            "BASE_DIR. It won't be registered automatically.",
            id="next.E046",
        )
        for cls_name, file_path in _outside_base_dir_classes
    ]


@register(Tags.compatibility)
def check_invalid_form_meta_scope(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when form class has invalid Meta.scope value."""
    return [
        Error(
            f"Form class {cls_name!r} has Meta.scope = {bad_value!r}. "
            "Valid values are 'page' and 'shared'.",
            id="next.E047",
        )
        for cls_name, bad_value in _invalid_meta_scope_classes
    ]


@register(Tags.compatibility)
def check_action_applied_to_class(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when @action decorator was applied to a class."""
    return [
        Error(
            f"@action was applied to class {cls_name!r}. "
            "Form classes register automatically through __init_subclass__.",
            id="next.E053",
        )
        for cls_name in _action_applied_to_class
    ]


__all__ = [
    "_action_collisions",
    "_handler_fingerprint",
    "check_action_applied_to_class",
    "check_form_action_backends_configuration",
    "check_form_action_collisions",
    "check_forms_outside_base_dir",
    "check_invalid_form_meta_scope",
    "clear_action_collisions",
    "record_possible_collision",
]
