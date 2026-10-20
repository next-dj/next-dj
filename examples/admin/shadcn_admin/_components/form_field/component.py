from django.forms.utils import flatatt
from django.utils.safestring import SafeString

from next import component
from next.forms import FieldSpec


# The template writes these itself, so echoing the widget's copy would duplicate them.
TEMPLATE_OWNED = frozenset(
    {
        "checked",
        "class",
        "cols",
        "id",
        "multiple",
        "name",
        "required",
        "rows",
        "selected",
        "size",
        "type",
        "value",
    }
)


@component.context("widget_attrs")
def widget_attrs(info: FieldSpec) -> SafeString:
    """Flatten the widget attributes Django derived, such as a decimal's `step`."""
    attrs = {
        name: value
        for name, value in info.bound.field.widget.attrs.items()
        if name not in TEMPLATE_OWNED
    }
    return flatatt(attrs)
