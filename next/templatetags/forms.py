"""Template tags for rendering next.forms form blocks."""

from typing import TYPE_CHECKING, cast

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import get_token
from django.utils.html import escape, format_html

from next.deps import RESERVED_KEYS
from next.forms import form_action_manager
from next.forms._request_utils import _url_kwargs_from_post
from next.forms.manager import build_form_namespace_for_action
from next.forms.uid import _validated_origin_path, page_path_token
from next.forms.widgets import bind_component_widgets


_NEXT_FORM_PAGE = "_next_form_page"
_NEXT_FORM_ORIGIN = "_next_form_origin"
_FORM_TAG_BITS = 2


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.template.base import FilterExpression


register = template.Library()


@register.tag(name="form")
def do_form(parser: template.base.Parser, token: template.base.Token) -> "FormNode":
    """Block tag accepting a single quoted action name."""
    bits = token.split_contents()
    if len(bits) != _FORM_TAG_BITS:
        msg = f"{bits[0]!r} tag requires exactly one argument: the action name"
        raise template.TemplateSyntaxError(msg)
    action_expr = parser.compile_filter(bits[1])
    nodelist = parser.parse(("endform",))
    parser.delete_first_token()
    return FormNode(action_expr=action_expr, nodelist=nodelist)


class FormNode(template.Node):
    """Render `<form>` with action URL, method=post, csrf_token."""

    __slots__ = ("action_expr", "nodelist")

    def __init__(
        self,
        action_expr: "FilterExpression",
        nodelist: template.NodeList,
    ) -> None:
        """Initialize with parsed action expression and template nodelist."""
        self.action_expr = action_expr
        self.nodelist = nodelist

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
        self,
        request: "HttpRequest",
        *,
        next_form_page: str | None,
    ) -> str:
        """Build CSRF, origin page, and URL parameter hidden inputs."""
        inputs = [
            format_html(
                '<input type="hidden" name="csrfmiddlewaretoken" value="{}">',
                get_token(request),
            )
        ]
        if next_form_page:
            inputs.append(
                format_html(
                    '<input type="hidden" name="{}" value="{}">',
                    _NEXT_FORM_PAGE,
                    escape(page_path_token(next_form_page)),
                )
            )
        origin = self._origin_path(request)
        if origin:
            inputs.append(
                format_html(
                    '<input type="hidden" name="{}" value="{}">',
                    _NEXT_FORM_ORIGIN,
                    escape(origin),
                )
            )

        for key, value in self._url_params(request).items():
            inputs.append(
                format_html(
                    '<input type="hidden" name="_url_param_{}" value="{}">',
                    escape(key),
                    escape(str(value)),
                )
            )

        return "\n".join(inputs)

    @staticmethod
    def _origin_path(request: "HttpRequest") -> str | None:
        """Return the page path the form belongs to.

        On the validation-error re-render the request targets the action
        endpoint, so the posted origin of the original page wins over
        `request.path`.
        """
        if getattr(request, "method", None) == "POST":
            posted = _validated_origin_path(request.POST.get(_NEXT_FORM_ORIGIN))
            if posted is not None:
                return posted
        return getattr(request, "path", None)

    @staticmethod
    def _url_params(request: "HttpRequest") -> dict[str, object]:
        """Return the URL kwargs of the page the form belongs to.

        On the validation-error re-render the resolver match holds only the
        dispatch `uid`, so the `_url_param_*` fields posted from the original
        page win.
        """
        params: dict[str, object] = {}
        if request.resolver_match and request.resolver_match.kwargs:
            params = {
                key: value
                for key, value in request.resolver_match.kwargs.items()
                if key != "uid" and key not in RESERVED_KEYS
            }
        if not params:
            params = _url_kwargs_from_post(request)
        return params

    def render(self, context: template.Context) -> str:
        """Render form tag with action URL, method=post, CSRF, and content."""
        request = self._get_request(context)

        action_name = str(self.action_expr.resolve(context))

        raw_page = context.get("current_page_module_path")
        next_form_page = str(raw_page) if raw_page else None

        try:
            action_url = form_action_manager.get_action_url(
                action_name, page_path=next_form_page
            )
        except KeyError:
            msg = f"Unknown form action: {action_name!r}"
            raise RuntimeError(msg) from None

        opening_tag = format_html(
            '<form action="{}" method="post">',
            escape(action_url),
        )
        hidden_inputs = self._build_hidden_inputs(
            request,
            next_form_page=next_form_page,
        )

        form_obj = context.get(action_name)
        if form_obj and hasattr(form_obj, "form"):
            form_instance = form_obj.form
            wizard_instance = getattr(form_obj, "wizard", None)
        else:
            built = build_form_namespace_for_action(
                action_name, request, page_path=next_form_page
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

        push_kwargs: dict[str, object] = {"form": form_instance}
        if wizard_instance is not None:
            push_kwargs["wizard"] = wizard_instance
        with context.push(**push_kwargs):
            content = self.nodelist.render(context)

        return f"{opening_tag}\n{hidden_inputs}\n{content}\n</form>"
