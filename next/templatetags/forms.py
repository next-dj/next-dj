"""Template tags for rendering next.forms form blocks."""

from typing import TYPE_CHECKING, cast

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import get_token
from django.utils.html import format_html

from next.forms.backends import FormActionNotFoundError
from next.forms.manager import _build_form_namespace_from_meta, form_action_manager
from next.forms.uid import (
    FORM_ORIGIN_OVERRIDE_KEY,
    ORIGIN_FIELD_NAME,
    validated_origin_path,
)
from next.forms.widgets import bind_component_widgets


_MIN_FORM_TAG_BITS = 2
_RESERVED_FORM_ATTRS = frozenset({"action", "method"})
_RESERVED_FORM_ATTR_PREFIX = "data-next-"

# Python params of the tag that compile to client `data-next-*` attributes
# on the form. The server authors these names, the client reads them, the
# markup never carries a raw selector or swap mode.
_PARTIAL_FORM_PARAMS: dict[str, str] = {
    "validate": "data-next-validate",
    "trigger": "data-next-trigger",
    "debounce": "data-next-debounce",
    "zone": "data-next-target",
    "key": "data-next-key",
}


if TYPE_CHECKING:
    from django import forms as django_forms
    from django.http import HttpRequest
    from django.template.base import FilterExpression


register = template.Library()


@register.tag(name="form")
def do_form(parser: template.base.Parser, token: template.base.Token) -> "FormNode":
    """Block tag accepting an action name plus optional HTML attributes.

    The `validate`, `trigger`, `debounce`, `zone`, and `key` params compile
    to client `data-next-*` attributes on the form, every other key="value"
    pair stays a plain HTML attribute.
    """
    bits = token.split_contents()
    if len(bits) < _MIN_FORM_TAG_BITS:
        msg = f"{bits[0]!r} tag requires the action name as its first argument"
        raise template.TemplateSyntaxError(msg)
    action_expr = parser.compile_filter(bits[1])
    attrs: list[tuple[str, FilterExpression]] = []
    partial_attrs: list[tuple[str, FilterExpression]] = []
    for bit in bits[2:]:
        name, eq, value = bit.partition("=")
        if eq and name in _PARTIAL_FORM_PARAMS:
            partial_attrs.append(
                (_PARTIAL_FORM_PARAMS[name], parser.compile_filter(value))
            )
            continue
        attrs.append(_parse_form_attr(parser, bits[0], bit))
    nodelist = parser.parse(("endform",))
    parser.delete_first_token()
    return FormNode(
        action_expr=action_expr,
        nodelist=nodelist,
        attrs=tuple(attrs),
        partial_attrs=tuple(partial_attrs),
    )


def _page_path_from_context(context: template.Context) -> str | None:
    """Return the current page module path stored in the render context."""
    raw_page = context.get("current_page_module_path")
    return str(raw_page) if raw_page else None


@register.simple_tag(takes_context=True)
def action_url(context: template.Context, action_name: str) -> str:
    """Return the endpoint URL for an action, page-scoped like `{% form %}`."""
    name = str(action_name)
    if not name:
        msg = (
            "{% action_url %} resolved its argument to an empty action name. "
            "An unquoted name is looked up as a template variable, quote the "
            "action name to pass it as a literal."
        )
        raise FormActionNotFoundError(msg)
    return form_action_manager.get_action_url(
        name, page_path=_page_path_from_context(context)
    )


def _parse_form_attr(
    parser: template.base.Parser,
    tag_name: str,
    bit: str,
) -> "tuple[str, FilterExpression]":
    """Parse one `key="value"` tag argument into an attribute name and value."""
    name, eq, value = bit.partition("=")
    if not eq or not name or not value:
        msg = (
            f"{tag_name!r} tag arguments after the action name must use "
            f'the key="value" form, got {bit!r}'
        )
        raise template.TemplateSyntaxError(msg)
    if name in _RESERVED_FORM_ATTRS or name.startswith(_RESERVED_FORM_ATTR_PREFIX):
        msg = f"{tag_name!r} tag reserves the {name!r} attribute for the framework"
        raise template.TemplateSyntaxError(msg)
    return name, parser.compile_filter(value)


class FormNode(template.Node):
    """Render `<form>` with action URL, method=post, csrf_token."""

    __slots__ = ("action_expr", "attrs", "nodelist", "partial_attrs")

    def __init__(
        self,
        action_expr: "FilterExpression",
        nodelist: template.NodeList,
        attrs: "tuple[tuple[str, FilterExpression], ...]" = (),
        partial_attrs: "tuple[tuple[str, FilterExpression], ...]" = (),
    ) -> None:
        """Initialize with the action, nodelist, HTML attrs, and partial attrs."""
        self.action_expr = action_expr
        self.nodelist = nodelist
        self.attrs = attrs
        self.partial_attrs = partial_attrs

    def _get_request(self, context: template.Context) -> "HttpRequest":
        """Extract request from context or raise ImproperlyConfigured."""
        request = context.get("request")
        if request is None:
            msg = (
                "{% form %} requires 'request' in template context. "
                "Add 'django.template.context_processors.request' to "
                "TEMPLATES[*].OPTIONS.context_processors."
            )
            raise ImproperlyConfigured(msg)
        return cast("HttpRequest", request)

    def _build_hidden_inputs(
        self, context: template.Context, request: "HttpRequest"
    ) -> str:
        """Build the CSRF and origin hidden inputs."""
        inputs = [
            format_html(
                '<input type="hidden" name="csrfmiddlewaretoken" value="{}">',
                get_token(request),
            )
        ]
        origin = self._origin_path(context, request)
        if origin:
            inputs.append(
                format_html(
                    '<input type="hidden" name="{}" value="{}">',
                    ORIGIN_FIELD_NAME,
                    origin,
                )
            )

        return "\n".join(inputs)

    @staticmethod
    def _origin_path(context: template.Context, request: "HttpRequest") -> str | None:
        """Return the page path the form belongs to.

        On the validation-error re-render the request targets the action
        endpoint, so the posted origin of the original page wins over
        `request.path`. On a wizard advance the shaping layer merges the
        next step URL under FORM_ORIGIN_OVERRIDE_KEY into the zone render
        context, which wins over the submitted step origin so blur-validate
        probes on the new step render from the correct page.
        """
        override = context.get(FORM_ORIGIN_OVERRIDE_KEY)
        if override is not None:
            return str(override)
        if getattr(request, "method", None) == "POST":
            posted = validated_origin_path(request.POST.get(ORIGIN_FIELD_NAME))
            if posted is not None:
                return posted
        return getattr(request, "path", None)

    def _opening_tag(
        self,
        context: template.Context,
        action_url: str,
        uid: str | None,
        form_instance: "django_forms.Form | None",
    ) -> str:
        """Build the opening form element with framework and extra attributes."""
        bits: list[str] = [format_html('<form action="{}" method="post"', action_url)]
        if uid:
            bits.append(format_html('data-next-action="{}"', uid))
        bits.extend(
            format_html('{}="{}"', name, str(expr.resolve(context)))
            for name, expr in self.partial_attrs
        )
        if (
            form_instance is not None
            and form_instance.is_multipart()
            and all(name != "enctype" for name, _expr in self.attrs)
        ):
            bits.append('enctype="multipart/form-data"')
        bits.extend(
            format_html('{}="{}"', name, str(expr.resolve(context)))
            for name, expr in self.attrs
        )
        return " ".join(bits) + ">"

    def render(self, context: template.Context) -> str:
        """Render form tag with action URL, method=post, CSRF, and content."""
        request = self._get_request(context)

        action_name = str(self.action_expr.resolve(context))
        if not action_name:
            token = self.action_expr.token
            msg = (
                f"{{% form {token} %}} resolved to an empty action name. "
                f"An unquoted name is looked up as a template variable, "
                f'write {{% form "{token}" %}} to pass the action name as '
                "a literal."
            )
            raise FormActionNotFoundError(msg, name=token)

        page_path = _page_path_from_context(context)

        resolved_action_url = form_action_manager.get_action_url(
            action_name, page_path=page_path
        )
        meta = form_action_manager.get_action_meta(action_name, page_path=page_path)

        form_obj = context.get(action_name)
        if form_obj and hasattr(form_obj, "form"):
            form_instance = form_obj.form
            wizard_instance = getattr(form_obj, "wizard", None)
        else:
            built = (
                _build_form_namespace_from_meta(meta, request)
                if meta is not None
                else None
            )
            form_instance = built.form if built is not None else None
            wizard_instance = getattr(built, "wizard", None) if built else None

        if form_instance is not None:
            bind_component_widgets(
                form_instance,
                template_path=context.get("current_template_path"),
                request=request,
                collector=context.get("_static_collector"),
                with_errors=form_instance.is_bound,
            )

        opening_tag = self._opening_tag(
            context,
            resolved_action_url,
            meta.get("uid") if meta is not None else None,
            form_instance,
        )
        hidden_inputs = self._build_hidden_inputs(context, request)

        push_kwargs: dict[str, object] = {"form": form_instance}
        if wizard_instance is not None:
            push_kwargs["wizard"] = wizard_instance
        with context.push(**push_kwargs):
            content = self.nodelist.render(context)

        return f"{opening_tag}\n{hidden_inputs}\n{content}\n</form>"
