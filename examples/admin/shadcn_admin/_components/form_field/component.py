from django.forms.utils import flatatt
from django.utils.safestring import SafeString

from next import component
from next.forms import FieldSpec


# The template writes these itself, so echoing the widget's copy would either
# duplicate the attribute or overwrite the shadcn styling with Django's own.
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
    """Flatten the attributes Django computed for the widget.

    A hand-written input carries only what the template spells out, so the constraints
    the form field derived are lost and a decimal renders as a whole-number input.
    """
    attrs = {
        name: value
        for name, value in info.bound.field.widget.attrs.items()
        if name not in TEMPLATE_OWNED
    }
    return flatatt(attrs)
