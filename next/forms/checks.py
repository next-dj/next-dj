"""System checks for the forms subsystem."""

from pathlib import Path
from typing import TYPE_CHECKING

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
from next.conf import import_class_cached, next_framework_settings

from .backends import FormActionBackend
from .diagnostics import registration_diagnostics
from .manager import form_action_manager
from .widgets import ComponentWidget
from .wizard import (
    CacheFormWizardBackend,
    FormWizardBackend,
    SessionFormWizardBackend,
)


if TYPE_CHECKING:
    from collections.abc import Iterator

    from .backends import ActionMeta


_FORM_ACTION_BACKEND_SETTINGS_KEY = "FORM_ACTION_BACKENDS"
_FORM_WIZARD_BACKEND_SETTINGS_KEY = "FORM_WIZARD_BACKEND"
_FORM_ANCHOR_FILES_SETTINGS_KEY = "FORM_ANCHOR_FILES"


def _iter_registered_actions() -> "Iterator[ActionMeta]":
    """Yield every action meta from every configured form-action backend."""
    for backend in form_action_manager.backends:
        yield from backend.iter_actions()


@register(Tags.compatibility)
def check_form_action_collisions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Flag two `@action` calls that share a name but come from different handlers."""
    return [
        Error(
            f"Form action {name!r} is registered by {len(fps)} different "
            "handlers. Rename one of them or move one to a different scope "
            "to avoid the collision.",
            obj=settings,
            id="next.E041",
        )
        for name, fps in registration_diagnostics.action_collisions.items()
    ]


@register(Tags.compatibility)
def check_shared_action_name_collisions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when one shared action name is declared by two different modules."""
    return [
        Error(
            f"Shared form action {name!r} is declared in "
            f"{len(scope_keys)} modules: "
            f"{', '.join(repr(key) for key in sorted(scope_keys))}. "
            "Lookups by bare name resolve to whichever module imported "
            "first. Rename one class or set Meta.scope = 'page' on one "
            "of them.",
            obj=settings,
            id="next.E046",
        )
        for name, scope_keys in registration_diagnostics.shared_name_collisions.items()
    ]


@register(Tags.compatibility)
def check_form_action_backends_configuration(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
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
        for cls_name, file_path in registration_diagnostics.outside_base_dir
    ]


@register(Tags.compatibility)
def check_invalid_form_meta_scope(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a form class Meta.scope or an @action scope is invalid."""
    class_errors: list[CheckMessage] = [
        Error(
            f"Form class {cls_name!r} has Meta.scope = {bad_value!r}. "
            "Valid values are 'page' and 'shared'.",
            id="next.E047",
        )
        for cls_name, bad_value in registration_diagnostics.invalid_meta_scope
    ]
    action_errors: list[CheckMessage] = [
        Error(
            f"Action {qualname!r} declares scope={bad_value!r}. "
            "Valid values are 'page' and 'shared'.",
            id="next.E047",
        )
        for qualname, bad_value in registration_diagnostics.invalid_action_scope
    ]
    return class_errors + action_errors


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
        for cls_name in registration_diagnostics.action_applied_to_class
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
        for (
            cls_name,
            model_label,
            field,
        ) in registration_diagnostics.instance_from_url_unknown_field
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
        for cls_name in registration_diagnostics.instance_from_url_on_non_model_form
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
        for cls_name in registration_diagnostics.wizard_without_steps
    ]


@register(Tags.compatibility)
def check_form_wizard_backend(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate `FORM_WIZARD_BACKEND` shape and import path."""
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
def check_form_wizard_sessions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when wizard storage needs sessions without django.contrib.sessions."""
    if "django.contrib.sessions" in settings.INSTALLED_APPS:
        return []
    if not any(meta.get("wizard_class") for meta in _iter_registered_actions()):
        return []
    config = next_framework_settings.FORM_WIZARD_BACKEND
    backend_path = config.get("BACKEND") if isinstance(config, dict) else None
    if not isinstance(backend_path, str):
        return []
    try:
        cls = import_class_cached(backend_path)
    except ImportError:
        return []
    session_bound = isinstance(cls, type) and issubclass(
        cls, (CacheFormWizardBackend, SessionFormWizardBackend)
    )
    if not session_bound:
        return []
    return [
        DjangoWarning(
            "FormWizard subclasses are registered and the configured wizard "
            "backend needs Django sessions to store steps, but "
            "django.contrib.sessions is not in INSTALLED_APPS. Saving a step "
            "will raise ImproperlyConfigured at request time.",
            obj=settings,
            id="next.W056",
        ),
    ]


@register(Tags.compatibility)
def check_wizard_step_actions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when a wizard step class is also a registered standalone action.

    Only static Meta.steps are inspected, `get_steps` dynamics are not visible.
    """
    metas = list(_iter_registered_actions())
    action_names: dict[type, str] = {}
    for meta in metas:
        form_class = meta.get("form_class")
        name = meta.get("name")
        if isinstance(form_class, type) and name is not None:
            action_names.setdefault(form_class, name)
    messages: list[CheckMessage] = []
    for meta in metas:
        wizard_class = meta.get("wizard_class")
        if wizard_class is None:
            continue
        static_steps = getattr(wizard_class, "_static_steps", None)
        if static_steps is None:
            continue
        for step_name, step_class in static_steps():
            action_name = action_names.get(step_class)
            if action_name is None:
                continue
            messages.append(
                DjangoWarning(
                    f"Form {step_class.__name__!r} is registered as action "
                    f"{action_name!r} and is also step {step_name!r} of wizard "
                    f"{wizard_class.__name__!r}. Subclass django.forms "
                    "directly or set Meta.abstract = True on the step form.",
                    id="next.W057",
                )
            )
    return messages


_PAGE_MODULE_NAME = "page.py"


@register(Tags.compatibility)
def check_wizard_url_param_route(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a page-scoped wizard's page path lacks the url_param segment.

    Only wizards declared in a page module are inspected. The page file
    path maps one to one onto the route, so a missing segment is a
    definite misconfiguration. Wizards declared in shared or component
    modules have no statically known route and are skipped.
    """
    messages: list[CheckMessage] = []
    for meta in _iter_registered_actions():
        wizard_class = meta.get("wizard_class")
        if wizard_class is None or meta.get("scope") != "page":
            continue
        file_path = meta.get("file_path")
        if not file_path:
            continue
        path = Path(file_path)
        if path.name != _PAGE_MODULE_NAME:
            continue
        url_param = getattr(getattr(wizard_class, "Meta", None), "url_param", "step")
        if f"[{url_param}]" in path.parts:
            continue
        messages.append(
            Error(
                f"FormWizard {wizard_class.__name__!r} is declared in "
                f"{file_path} but no [{url_param}] directory appears on that "
                "page path, so the wizard can never advance past its first "
                f"step. Add a [{url_param}] route segment or set "
                "Meta.url_param to the captured kwarg name.",
                id="next.E054",
            )
        )
    return messages


@register(Tags.compatibility)
def check_wizard_step_file_fields(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when a static wizard step declares a FileField or ImageField.

    Only static Meta.steps are inspected, `get_steps` dynamics are not visible.
    """
    messages: list[CheckMessage] = []
    for meta in _iter_registered_actions():
        wizard_class = meta.get("wizard_class")
        if wizard_class is None:
            continue
        static_steps = getattr(wizard_class, "_static_steps", None)
        if static_steps is None:
            continue
        for step_name, step_class in static_steps():
            base_fields = getattr(step_class, "base_fields", {})
            if not any(isinstance(field, FileField) for field in base_fields.values()):
                continue
            messages.append(
                DjangoWarning(
                    f"FormWizard {wizard_class.__name__!r} step {step_name!r} "
                    "declares a FileField. Wizard storage persists "
                    "cleaned_data between requests and uploaded files do not "
                    "survive that round-trip. Collect the upload in a "
                    "standalone form action instead.",
                    id="next.W058",
                )
            )
    return messages


@register(Tags.compatibility)
def check_wizard_step_field_collisions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when two static wizard steps declare the same field name.

    Only static Meta.steps are inspected, `get_steps` dynamics are not visible.
    """
    messages: list[CheckMessage] = []
    for meta in _iter_registered_actions():
        wizard_class = meta.get("wizard_class")
        if wizard_class is None:
            continue
        static_steps = getattr(wizard_class, "_static_steps", None)
        if static_steps is None:
            continue
        owners: dict[str, list[str]] = {}
        for step_name, step_class in static_steps():
            for field_name in getattr(step_class, "base_fields", {}):
                owners.setdefault(field_name, []).append(step_name)
        for field_name, step_names in owners.items():
            if len(step_names) == 1:
                continue
            rendered = ", ".join(repr(step_name) for step_name in step_names)
            messages.append(
                DjangoWarning(
                    f"Wizard {wizard_class.__name__!r}: field {field_name!r} "
                    f"is declared by steps {rendered}. get_all_cleaned_data() "
                    "keeps the last value, use get_cleaned_data_for_step() "
                    "for per-step access.",
                    id="next.W059",
                )
            )
    return messages


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
    # Only a list round-trips through the settings merge, so tuples and sets
    # are rejected here instead of silently falling back to the defaults.
    if not isinstance(value, list):
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must be None or a list of strings.",
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
def check_action_guard_permissions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when permission_required is declared without django.contrib.auth."""
    if "django.contrib.auth" in settings.INSTALLED_APPS:
        return []
    return [
        DjangoWarning(
            f"Form action {meta.get('name')!r} declares permission_required "
            "but django.contrib.auth is not in INSTALLED_APPS, so the "
            "permission check cannot resolve users or permissions.",
            obj=settings,
            id="next.W060",
        )
        for meta in _iter_registered_actions()
        if (guard := meta.get("guard")) is not None and guard.permissions
    ]


_MESSAGE_MIDDLEWARE = "django.contrib.messages.middleware.MessageMiddleware"


def _declares_success_message(target: object) -> bool:
    """Return True when the class declares a non-empty Meta.success_message."""
    return bool(getattr(getattr(target, "Meta", None), "success_message", ""))


@register(Tags.compatibility)
def check_success_message_framework(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when Meta.success_message is declared without the messages framework."""
    has_app = "django.contrib.messages" in settings.INSTALLED_APPS
    has_middleware = _MESSAGE_MIDDLEWARE in tuple(settings.MIDDLEWARE or ())
    if has_app and has_middleware:
        return []
    return [
        DjangoWarning(
            f"Form action {meta.get('name')!r} declares Meta.success_message "
            "but the messages framework is not fully installed. Add "
            "django.contrib.messages to INSTALLED_APPS and MessageMiddleware "
            "to MIDDLEWARE, or the submission will raise MessageFailure.",
            obj=settings,
            id="next.W061",
        )
        for meta in _iter_registered_actions()
        if _declares_success_message(meta.get("form_class"))
        or _declares_success_message(meta.get("wizard_class"))
    ]


@register(Tags.compatibility)
def check_component_widget_components(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when a ComponentWidget names a component that does not resolve."""
    base = getattr(settings, "BASE_DIR", None)
    seen: set[str] = set()
    messages: list[CheckMessage] = []
    for meta in _iter_registered_actions():
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
    for meta in _iter_registered_actions():
        form_class = meta.get("form_class")
        if not isinstance(form_class, type):
            continue
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
    "check_action_guard_permissions",
    "check_component_widget_components",
    "check_component_widget_field_types",
    "check_form_action_backends_configuration",
    "check_form_action_collisions",
    "check_form_anchor_files",
    "check_form_wizard_backend",
    "check_form_wizard_sessions",
    "check_form_wizard_steps",
    "check_forms_outside_base_dir",
    "check_instance_from_url_on_non_model_form",
    "check_instance_from_url_unknown_field",
    "check_invalid_form_meta_scope",
    "check_shared_action_name_collisions",
    "check_success_message_framework",
    "check_wizard_step_actions",
    "check_wizard_step_field_collisions",
    "check_wizard_step_file_fields",
    "check_wizard_url_param_route",
]
