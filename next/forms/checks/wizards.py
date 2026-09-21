"""System checks for the `FormWizard` subclasses a project registers.

The ids are `next.E050` and `next.E054` for a stepless wizard and a page path with no
step segment, and `next.W056` to `next.W059` for the runtime hazards.
"""

from pathlib import Path

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.forms import FileField

from next.checks import NEXT
from next.conf import import_class_cached, next_framework_settings
from next.forms.wizard import CacheFormWizardBackend, SessionFormWizardBackend

from .sources import diagnostics, iter_registered_actions


_PAGE_MODULE_NAME = "page.py"


@register(NEXT)
def check_form_wizard_steps(*args, **kwargs) -> list[CheckMessage]:
    """Error when a FormWizard declares no steps."""
    return [
        Error(
            f"FormWizard {cls_name!r} has no Meta.steps or Meta.steps is empty. "
            "Declare steps as a list of (name, FormClass) tuples.",
            id="next.E050",
        )
        for cls_name in diagnostics().wizard_without_steps
    ]


@register(NEXT)
def check_form_wizard_sessions(*args, **kwargs) -> list[CheckMessage]:
    """Warn when wizard storage needs sessions without django.contrib.sessions."""
    if "django.contrib.sessions" in settings.INSTALLED_APPS:
        return []
    if not any(meta.get("wizard_class") for meta in iter_registered_actions()):
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
        )
    ]


@register(NEXT)
def check_wizard_step_actions(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a wizard step class is also a registered standalone action.

    Only static Meta.steps are inspected, `get_steps` dynamics are not visible.
    """
    metas = list(iter_registered_actions())
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


@register(NEXT)
def check_wizard_url_param_route(*args, **kwargs) -> list[CheckMessage]:
    """Error when a page-scoped wizard's page path lacks the url_param segment.

    Only wizards declared in a page module are inspected, since the page file path
    maps one to one onto the route and a missing segment is a definite misconfiguration.
    """
    messages: list[CheckMessage] = []
    for meta in iter_registered_actions():
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


@register(NEXT)
def check_wizard_step_file_fields(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a static wizard step declares a FileField or ImageField.

    Only static Meta.steps are inspected, `get_steps` dynamics are not visible.
    """
    messages: list[CheckMessage] = []
    for meta in iter_registered_actions():
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


@register(NEXT)
def check_wizard_step_field_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Warn when two static wizard steps declare the same field name.

    Only static Meta.steps are inspected, `get_steps` dynamics are not visible.
    """
    messages: list[CheckMessage] = []
    for meta in iter_registered_actions():
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


__all__ = [
    "check_form_wizard_sessions",
    "check_form_wizard_steps",
    "check_wizard_step_actions",
    "check_wizard_step_field_collisions",
    "check_wizard_step_file_fields",
    "check_wizard_url_param_route",
]
