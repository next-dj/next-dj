"""System checks for the `ComponentWidget` a form field carries.

The ids are `next.W054` for an unknown component and `next.W055` for a field pairing.
"""

from pathlib import Path

from django.conf import settings
from django.core.checks import CheckMessage, Warning as DjangoWarning, register
from django.forms import FileField, MultiValueField

from next.checks import NEXT
from next.components.facade import get_component
from next.forms.widgets import ComponentWidget

from .sources import iter_registered_actions


@register(NEXT)
def check_component_widget_components(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a ComponentWidget names a component that does not resolve."""
    base = getattr(settings, "BASE_DIR", None)
    seen: set[str] = set()
    messages: list[CheckMessage] = []
    for meta in iter_registered_actions():
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


@register(NEXT)
def check_component_widget_field_types(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a ComponentWidget mispairs with a file field or a MultiValueField."""
    messages: list[CheckMessage] = []
    for meta in iter_registered_actions():
        form_class = meta.get("form_class")
        if not isinstance(form_class, type):
            continue
        base_fields = getattr(form_class, "base_fields", None)
        if base_fields is None:
            continue
        for field_name, field in base_fields.items():
            widget = field.widget
            if not isinstance(widget, ComponentWidget):
                continue
            if widget.needs_multipart_form == isinstance(
                field, FileField
            ) and not isinstance(field, MultiValueField):
                continue
            field_label = f"{form_class.__name__}.{field_name}"
            field_type = type(field).__name__
            messages.append(
                DjangoWarning(
                    f"{type(widget).__name__} is attached to {field_label} which "
                    f"is a {field_type}. ComponentWidget supports single-value "
                    "text-like fields, a FileField takes ComponentFileWidget, "
                    "and MultiValueField is not supported.",
                    id="next.W055",
                )
            )
    return messages


__all__ = ["check_component_widget_components", "check_component_widget_field_types"]
