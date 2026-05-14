"""Template tag ``{% form %}`` for next-dj form actions.

Parses ``@action`` and other attributes, inserts the CSRF token, and emits POST forms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import get_token
from django.utils.html import escape, format_html

from next.deps import RESERVED_KEYS
from next.forms import build_form_namespace_for_action, form_action_manager


_NEXT_FORM_PAGE = "_next_form_page"
_NEXT_FORM_ORIGIN = "_next_form_origin"


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.template.base import FilterExpression


register = template.Library()


_ARG_PATTERN = re.compile(
    r"(@?[\w.-]+)\s*=\s*"
    r'(?:"((?:[^"\\]|\\.)*)"'  # double-quoted
    r"|'((?:[^'\\]|\\.)*)'"  # single-quoted
    r"|(\S+))"  # unquoted
)

_RESERVED_KEYS = frozenset({"action", "method"})
MIN_FORM_TAG_BITS = 2


def _parse_form_tag_args(contents: str) -> dict[str, str]:
    """Parse tag contents into key=value dict of bare strings.

    Quoted values lose their quotes here. The unquoted form is taken
    verbatim. Use `_parse_form_tag_tokens` when the value will be
    handed to `parser.compile_filter`, which expects quotes preserved
    so it can tell literals from variable lookups.
    """
    out: dict[str, str] = {}
    for m in _ARG_PATTERN.finditer(contents):
        key = m.group(1).strip()
        value = m.group(2) or m.group(3) or m.group(4)
        if m.group(4) is not None:
            value = value.strip().strip("'\"").strip()
        out[key] = value
    return out


def _parse_form_tag_tokens(contents: str) -> dict[str, str]:
    """Parse tag contents preserving quote style for `compile_filter`.

    Double-quoted values come back as ``'"value"'``, single-quoted as
    ``"'value'"``, unquoted as the bare token. This is the shape Django's
    ``parser.compile_filter`` expects so that ``@action="admin:add"`` is
    treated as a string literal while ``@action=state.action_name`` is
    treated as a variable lookup that resolves at render time.
    """
    out: dict[str, str] = {}
    for m in _ARG_PATTERN.finditer(contents):
        key = m.group(1).strip()
        if m.group(2) is not None:
            token = f'"{m.group(2)}"'
        elif m.group(3) is not None:
            token = f"'{m.group(3)}'"
        else:
            token = m.group(4).strip()
        out[key] = token
    return out


@dataclass(frozen=True, slots=True)
class FormConfig:
    """Immutable configuration parsed from `{% form %}` tag arguments.

    Both `action_expr` and `html_attrs` carry `FilterExpression` objects
    so string literals (`"admin:save"`) and context variables
    (`state.action_name`, `form_state.css_class|default:""`) work the
    same way and resolve at render time.
    """

    action_expr: FilterExpression
    html_attrs: tuple[tuple[str, FilterExpression], ...] = ()

    @classmethod
    def from_tag_args(
        cls,
        args: dict[str, str],
        parser: template.base.Parser,
    ) -> FormConfig:
        """Build FormConfig by compiling every value into a FilterExpression."""
        raw_action = args.get("@action") or args.get("action")
        if not raw_action:
            msg = "{% form %} tag requires @action='action_name'"
            raise template.TemplateSyntaxError(msg)

        action_expr = parser.compile_filter(raw_action)
        html_attrs = tuple(
            (k, parser.compile_filter(v))
            for k, v in args.items()
            if not k.startswith("@") and k not in _RESERVED_KEYS
        )

        return cls(action_expr=action_expr, html_attrs=html_attrs)


@dataclass(slots=True)
class FormAttrsBuilder:
    """Build the `<form ...>` opening tag from resolved values."""

    action_url: str = ""
    html_attrs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_resolved(
        cls,
        action_name: str,
        html_attrs: tuple[tuple[str, str], ...],
    ) -> FormAttrsBuilder:
        """Look up the action's URL and pair it with already-resolved HTML attrs."""
        try:
            action_url = form_action_manager.get_action_url(action_name)
        except KeyError:
            action_url = ""
        return cls(action_url=action_url, html_attrs=html_attrs)

    def build_opening_tag(self) -> str:
        """Build `<form action="..." method="post" ...>`."""
        parts = ['<form action="{}" method="post"']
        values: list[str] = [escape(self.action_url)]

        for name, value in self.html_attrs:
            parts.append(' {}="{}"')
            values.extend([escape(name), escape(str(value))])

        parts.append(">")
        return format_html("".join(parts), *values)


@register.tag(name="form")
def do_form(parser: template.base.Parser, token: template.base.Token) -> FormNode:
    """Block tag for `{% form %}` with @action."""
    bits = token.split_contents()
    if len(bits) < MIN_FORM_TAG_BITS:
        msg = f"{bits[0]!r} tag requires at least @action='...'"
        raise template.TemplateSyntaxError(msg) from None

    tokens = _parse_form_tag_tokens(" ".join(bits[1:]))
    config = FormConfig.from_tag_args(tokens, parser)

    nodelist = parser.parse(("endform",))
    parser.delete_first_token()

    return FormNode(config=config, nodelist=nodelist)


class FormNode(template.Node):
    """Render `<form>` with action URL, method="post", csrf_token."""

    __slots__ = ("config", "nodelist")

    def __init__(self, config: FormConfig, nodelist: template.NodeList) -> None:
        """Initialize with parsed config and template nodelist."""
        self.config = config
        self.nodelist = nodelist

    def _get_request(self, context: template.Context) -> HttpRequest:
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
        request: HttpRequest,
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
                    escape(next_form_page),
                )
            )
        origin = getattr(request, "path", None)
        if origin:
            inputs.append(
                format_html(
                    '<input type="hidden" name="{}" value="{}">',
                    _NEXT_FORM_ORIGIN,
                    escape(origin),
                )
            )

        if request.resolver_match and request.resolver_match.kwargs:
            for key, value in request.resolver_match.kwargs.items():
                # Skip uid (form action URL) and names reserved for DI
                if key != "uid" and key not in RESERVED_KEYS:
                    inputs.append(
                        format_html(
                            '<input type="hidden" name="_url_param_{}" value="{}">',
                            escape(key),
                            escape(str(value)),
                        )
                    )

        return "\n".join(inputs)

    def render(self, context: template.Context) -> str:
        """Render form tag with action URL, method=post, CSRF, and content."""
        request = self._get_request(context)

        action_name = str(self.config.action_expr.resolve(context))
        html_attrs = tuple(
            (name, str(expr.resolve(context))) for name, expr in self.config.html_attrs
        )
        builder = FormAttrsBuilder.from_resolved(action_name, html_attrs)

        raw_page = context.get("current_page_module_path")
        if not raw_page:
            msg = (
                "{% form %} requires 'current_page_module_path' in template context "
                "(set when rendering file-based pages)."
            )
            raise ImproperlyConfigured(msg)
        next_form_page = str(raw_page)

        form_obj = context.get(action_name)
        if form_obj and hasattr(form_obj, "form"):
            form_instance = form_obj.form
        else:
            built = build_form_namespace_for_action(action_name, request)
            form_instance = built.form if built is not None else None

        opening_tag = builder.build_opening_tag()
        hidden_inputs = self._build_hidden_inputs(
            request,
            next_form_page=next_form_page,
        )

        with context.push(form=form_instance):
            content = self.nodelist.render(context)

        return f"{opening_tag}\n{hidden_inputs}\n{content}\n</form>"
