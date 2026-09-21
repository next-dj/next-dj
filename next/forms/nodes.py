"""The template node `{% form %}` compiles to, rendering one form block.

It lives here so the partial checks reach it without importing the tag shim.
"""

from typing import TYPE_CHECKING, cast, override

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import get_token
from django.utils.html import format_html

from next.forms.errors import FormActionNotFoundError
from next.forms.manager import (
    _build_form_namespace_from_meta,
    form_action_manager,
    resolve_component_anchor,
)
from next.forms.uid import (
    FORM_ORIGIN_OVERRIDE_KEY,
    ORIGIN_FIELD_NAME,
    current_origin_path,
    validated_origin_path,
)
from next.forms.widgets import bind_component_widgets
from next.seeding import (
    ACTION_ANCHOR_KEY,
    COLLECTOR_KEY,
    COMPONENT_MODULE_PATH_KEY,
    PAGE_MODULE_PATH_KEY,
    REQUEST_KEY,
    TEMPLATE_PATH_KEY,
    RenderFrame,
    ambient_frame,
)


if TYPE_CHECKING:
    from django import forms as django_forms
    from django.http import HttpRequest
    from django.template.base import FilterExpression

    from next.forms.backends import ActionMeta


def _page_path_from_context(context: template.Context) -> str | None:
    """Return the current page module path stored in the render context."""
    raw_page = context.get(PAGE_MODULE_PATH_KEY)
    return str(raw_page) if raw_page else None


def _component_path_from_context(context: template.Context) -> str | None:
    """Return the current component module path stored in the render context."""
    raw_component = context.get(COMPONENT_MODULE_PATH_KEY)
    return str(raw_component) if raw_component else None


def _action_anchor_from_context(context: template.Context) -> str | None:
    """Return the anchor the actions of the enclosing form resolve against."""
    raw_anchor = context.get(ACTION_ANCHOR_KEY)
    return str(raw_anchor) if raw_anchor else None


def anchor_lookup_from_context(
    context: template.Context, action_name: str
) -> "tuple[str | None, ActionMeta | None]":
    """Return the lookup anchor, with the meta when a scoped anchor wins.

    A field component sits under the anchor of the form around it as well as its own.
    """
    for anchor in (
        _component_path_from_context(context),
        _action_anchor_from_context(context),
    ):
        if anchor is None:
            continue
        meta = resolve_component_anchor(action_name, anchor)
        if meta is not None:
            return anchor, meta
    return _page_path_from_context(context), None


class FormNode(template.Node):
    """Render `<form>` with action URL, method=post, csrf_token."""

    def __init__(
        self,
        action_expr: "FilterExpression",
        nodelist: template.NodeList,
        attrs: "tuple[tuple[str, FilterExpression], ...]" = (),
        partial_attrs: "dict[str, FilterExpression] | None" = None,
    ) -> None:
        """Initialize with the action, nodelist, HTML attrs, and partial attrs."""
        self.action_expr = action_expr
        self.nodelist = nodelist
        self.attrs = attrs
        self.partial_attrs = partial_attrs or {}

    def has_partial_attr(self, attr: str) -> bool:
        """Return True when the tag compiled the given `data-next-*` attribute."""
        return attr in self.partial_attrs

    def _get_request(self, context: template.Context) -> "HttpRequest":
        """Extract request from context or raise ImproperlyConfigured."""
        request = context.get(REQUEST_KEY)
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
        """Return the page URL the form belongs to, query string included.

        `FORM_ORIGIN_OVERRIDE_KEY` wins over both the posted origin and the current
        URL, for a validation-error re-render and a wizard step advance alike.
        """
        override = context.get(FORM_ORIGIN_OVERRIDE_KEY)
        if override is not None:
            return str(override)
        if getattr(request, "method", None) == "POST":
            posted = validated_origin_path(
                request.POST.get(ORIGIN_FIELD_NAME), request=request
            )
            if posted is not None:
                return posted
        return current_origin_path(request)

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
            for name, expr in self.partial_attrs.items()
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

    @override
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

        anchor_path, meta = anchor_lookup_from_context(context, action_name)

        resolved_action_url = form_action_manager.get_action_url(
            action_name, page_path=anchor_path
        )
        if meta is None:
            meta = form_action_manager.get_action_meta(
                action_name, page_path=anchor_path
            )

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

        frame: RenderFrame | None = None
        if form_instance is not None:
            frame = bind_component_widgets(
                form_instance,
                template_path=context.get(TEMPLATE_PATH_KEY),
                request=request,
                collector=context.get(COLLECTOR_KEY),
                page_module_path=_page_path_from_context(context),
                action_anchor=anchor_path,
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
            content = self._render_body(context, frame)

        return f"{opening_tag}\n{hidden_inputs}\n{content}\n</form>"

    def _render_body(
        self, context: template.Context, frame: "RenderFrame | None"
    ) -> str:
        """Render the tag body, publishing `frame` for a widget born mid-render.

        A formset builds `empty_form` on access, so no bind reaches its widgets.
        """
        if frame is None:
            return self.nodelist.render(context)
        with ambient_frame(frame):
            return self.nodelist.render(context)


__all__ = ["FormNode", "anchor_lookup_from_context"]
