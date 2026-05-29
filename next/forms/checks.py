"""System checks for the forms subsystem."""

from pathlib import Path

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.forms import FileField, MultiValueField

from next.components.facade import get_component
from next.conf import import_class_cached

from .backends import (
    FormActionBackend,
    _action_collisions,
    clear_action_collisions,
    record_possible_collision,
)
from .base import (
    _instance_from_url_on_non_model_form,
    _instance_from_url_unknown_field,
    _invalid_meta_scope_classes,
    _outside_base_dir_classes,
)
from .decorators import _action_applied_to_class
from .manager import form_action_manager
from .widgets import ComponentWidget
from .wizard import FormWizardBackend, _wizard_without_steps


_FORM_ACTION_BACKEND_SETTINGS_KEY = "DEFAULT_FORM_ACTION_BACKENDS"
_FORM_WIZARD_BACKEND_SETTINGS_KEY = "DEFAULT_FORM_WIZARD_BACKEND"
_FORM_ANCHOR_FILES_SETTINGS_KEY = "FORM_ANCHOR_FILES"


@register(Tags.compatibility)
def check_form_action_collisions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Flag two `@action` calls that share a name but come from different handlers."""
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
            id="next.W046",
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


@register(Tags.compatibility)
def check_instance_from_url_unknown_field(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when Meta.instance_from_url references a field absent on the model."""
    return [
        Error(
            f"Form class {cls_name!r} sets Meta.instance_from_url referencing "
            f"{field!r}, which is not a field on {model_label}.",
            id="next.E048",
        )
        for cls_name, model_label, field in _instance_from_url_unknown_field
    ]


@register(Tags.compatibility)
def check_instance_from_url_on_non_model_form(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when Meta.instance_from_url is set on a class that is not a ModelForm."""
    return [
        Error(
            f"Form class {cls_name!r} sets Meta.instance_from_url but is not a "
            "ModelForm. Subclass next.forms.ModelForm to load instances by URL.",
            id="next.E049",
        )
        for cls_name in _instance_from_url_on_non_model_form
    ]


@register(Tags.compatibility)
def check_form_wizard_steps(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a FormWizard declares no steps."""
    return [
        Error(
            f"FormWizard {cls_name!r} has no Meta.steps or Meta.steps is empty. "
            "Declare steps as a list of (name, FormClass) tuples.",
            id="next.E050",
        )
        for cls_name in _wizard_without_steps
    ]


@register(Tags.compatibility)
def check_form_wizard_backend(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate `DEFAULT_FORM_WIZARD_BACKEND` shape and import path."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    config = raw.get(_FORM_WIZARD_BACKEND_SETTINGS_KEY)
    if config is None:
        return []
    return _validate_form_wizard_backend(config)


def _validate_form_wizard_backend(config: object) -> list[CheckMessage]:
    key = _FORM_WIZARD_BACKEND_SETTINGS_KEY
    if not isinstance(config, dict):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must be a dict with a BACKEND key.",
                obj=settings,
                id="next.E051",
            ),
        ]
    backend_path = config.get("BACKEND")
    if not isinstance(backend_path, str):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}].BACKEND must be a string.",
                obj=settings,
                id="next.E051",
            ),
        ]
    try:
        cls = import_class_cached(backend_path)
    except ImportError as exc:
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}].BACKEND {backend_path!r} cannot be "
                f"imported: {exc}.",
                obj=settings,
                id="next.E051",
            ),
        ]
    if not (isinstance(cls, type) and issubclass(cls, FormWizardBackend)):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}].BACKEND {backend_path!r} must subclass "
                "FormWizardBackend.",
                obj=settings,
                id="next.E051",
            ),
        ]
    return []


@register(Tags.compatibility)
def check_form_anchor_files(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate that FORM_ANCHOR_FILES is None or a collection of strings."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    value = raw.get(_FORM_ANCHOR_FILES_SETTINGS_KEY)
    if value is None:
        return []
    key = _FORM_ANCHOR_FILES_SETTINGS_KEY
    # A bare string is iterable but would split into single characters, so it
    # must be rejected rather than silently treated as a one-element collection.
    if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must be None or a list, tuple, or set "
                "of strings.",
                obj=settings,
                id="next.E052",
            ),
        ]
    if not all(isinstance(item, str) for item in value):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must contain only strings.",
                obj=settings,
                id="next.E052",
            ),
        ]
    return []


@register(Tags.compatibility)
def check_component_widget_components(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when a ComponentWidget names a component that does not resolve."""
    base = getattr(settings, "BASE_DIR", None)
    seen: set[str] = set()
    messages: list[CheckMessage] = []
    registry = getattr(form_action_manager.default_backend, "_registry", {})
    for meta in registry.values():
        form_class = meta.get("form_class")
        base_fields = getattr(form_class, "base_fields", None)
        if base_fields is None:
            continue
        anchor = meta.get("file_path") or base
        if anchor is None:
            continue
        for field in base_fields.values():
            widget = getattr(field, "widget", None)
            if not isinstance(widget, ComponentWidget):
                continue
            name = widget.component_name
            if name in seen:
                continue
            if get_component(name, Path(anchor)) is None:
                seen.add(name)
                messages.append(
                    DjangoWarning(
                        f"ComponentWidget references component {name!r} that "
                        "is not registered.",
                        id="next.W054",
                    )
                )
    return messages


@register(Tags.compatibility)
def check_component_widget_field_types(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when a ComponentWidget is attached to an unsupported field type."""
    messages: list[CheckMessage] = []
    registry = getattr(form_action_manager.default_backend, "_registry", {})
    for meta in registry.values():
        form_class = meta.get("form_class")
        base_fields = getattr(form_class, "base_fields", None)
        if base_fields is None:
            continue
        for field_name, field in base_fields.items():
            if not isinstance(field.widget, ComponentWidget):
                continue
            if not isinstance(field, FileField | MultiValueField):
                continue
            field_label = f"{form_class.__name__}.{field_name}"
            field_type = type(field).__name__
            messages.append(
                DjangoWarning(
                    f"ComponentWidget is attached to {field_label} which is a "
                    f"{field_type}. ComponentWidget supports single-value "
                    "text-like fields only. FileField and MultiValueField are "
                    "not supported.",
                    id="next.W055",
                )
            )
    return messages


__all__ = [
    "check_action_applied_to_class",
    "check_component_widget_components",
    "check_component_widget_field_types",
    "check_form_action_backends_configuration",
    "check_form_action_collisions",
    "check_form_anchor_files",
    "check_form_wizard_backend",
    "check_form_wizard_steps",
    "check_forms_outside_base_dir",
    "check_instance_from_url_on_non_model_form",
    "check_instance_from_url_unknown_field",
    "check_invalid_form_meta_scope",
    "clear_action_collisions",
    "record_possible_collision",
]
