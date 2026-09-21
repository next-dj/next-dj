"""System checks for the form classes and `@action` handlers a project registers.

The ids are `next.E041` and `next.E046` for colliding names, `next.W046` for a class
outside `BASE_DIR`, `next.E047` and `next.E085` for a bad scope, `next.E053` for
`@action` on a class, `next.E048` and `next.E049` for `Meta.instance_from_url`, and
`next.W060` and `next.W061` for a missing `django.contrib` app.
"""

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT

from .sources import diagnostics, iter_registered_actions


_MESSAGE_MIDDLEWARE = "django.contrib.messages.middleware.MessageMiddleware"


@register(NEXT)
def check_form_action_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Flag two `@action` calls that share a name but come from different handlers."""
    return [
        Error(
            f"Form action {name!r} is registered by {len(fps)} different "
            "handlers. Rename one of them or move one to a different scope "
            "to avoid the collision.",
            obj=settings,
            id="next.E041",
        )
        for name, fps in diagnostics().action_collisions.items()
    ]


@register(NEXT)
def check_shared_action_name_collisions(*args, **kwargs) -> list[CheckMessage]:
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
        for name, scope_keys in diagnostics().shared_name_collisions.items()
    ]


@register(NEXT)
def check_forms_outside_base_dir(*args, **kwargs) -> list[CheckMessage]:
    """Warn when form classes are declared outside BASE_DIR."""
    return [
        DjangoWarning(
            f"Form class {cls_name!r} declared in {file_path!r} which is outside "
            "BASE_DIR. It won't be registered automatically.",
            id="next.W046",
        )
        for cls_name, file_path in diagnostics().outside_base_dir
    ]


@register(NEXT)
def check_invalid_form_meta_scope(*args, **kwargs) -> list[CheckMessage]:
    """Error when a form class Meta.scope or an @action scope is invalid."""
    class_errors: list[CheckMessage] = [
        Error(
            f"Form class {cls_name!r} has Meta.scope = {bad_value!r}. "
            "Valid values are 'page' and 'shared'.",
            id="next.E047",
        )
        for cls_name, bad_value in diagnostics().invalid_meta_scope
    ]
    action_errors: list[CheckMessage] = [
        Error(
            f"Action {qualname!r} declares scope={bad_value!r}. "
            "Valid values are 'page' and 'shared'.",
            id="next.E085",
        )
        for qualname, bad_value in diagnostics().invalid_action_scope
    ]
    return class_errors + action_errors


@register(NEXT)
def check_action_applied_to_class(*args, **kwargs) -> list[CheckMessage]:
    """Error when @action decorator was applied to a class."""
    return [
        Error(
            f"@action was applied to class {cls_name!r}. "
            "Form classes register automatically through __init_subclass__.",
            id="next.E053",
        )
        for cls_name in diagnostics().action_applied_to_class
    ]


@register(NEXT)
def check_instance_from_url_unknown_field(*args, **kwargs) -> list[CheckMessage]:
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
        ) in diagnostics().instance_from_url_unknown_field
    ]


@register(NEXT)
def check_instance_from_url_on_non_model_form(*args, **kwargs) -> list[CheckMessage]:
    """Error when Meta.instance_from_url is set on a class that is not a ModelForm."""
    return [
        Error(
            f"Form class {cls_name!r} sets Meta.instance_from_url but is not a "
            "ModelForm. Subclass next.forms.ModelForm to load instances by URL.",
            id="next.E049",
        )
        for cls_name in diagnostics().instance_from_url_on_non_model_form
    ]


@register(NEXT)
def check_action_guard_permissions(*args, **kwargs) -> list[CheckMessage]:
    """Warn when permission_required is declared without django.contrib.auth.

    Only the static `ActionGuard` is inspected, the per-request hooks run user code.
    """
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
        for meta in iter_registered_actions()
        if (guard := meta.get("guard")) is not None and guard.permissions
    ]


def _declares_success_message(target: object) -> bool:
    """Return True when the class declares a non-empty Meta.success_message."""
    return bool(getattr(getattr(target, "Meta", None), "success_message", ""))


@register(NEXT)
def check_success_message_framework(*args, **kwargs) -> list[CheckMessage]:
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
        for meta in iter_registered_actions()
        if _declares_success_message(meta.get("form_class"))
        or _declares_success_message(meta.get("wizard_class"))
    ]


__all__ = [
    "check_action_applied_to_class",
    "check_action_guard_permissions",
    "check_form_action_collisions",
    "check_forms_outside_base_dir",
    "check_instance_from_url_on_non_model_form",
    "check_instance_from_url_unknown_field",
    "check_invalid_form_meta_scope",
    "check_shared_action_name_collisions",
    "check_success_message_framework",
]
