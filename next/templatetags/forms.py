"""Template tag {% form %} for next-dj form actions.

Parses @action and other HTML attributes, inserts csrf_token,
and outputs a <form> with method="post".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from django import template
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import get_token
from django.utils.html import escape, format_html

from next.forms import form_action_manager


if TYPE_CHECKING:
    from django.http import HttpRequest


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
    """Parse tag contents into key=value dict. Supports @action, etc."""
    out: dict[str, str] = {}
    for m in _ARG_PATTERN.finditer(contents):
        key = m.group(1).strip()
        value = m.group(2) or m.group(3) or m.group(4)
        if m.group(4) is not None:
            value = value.strip().strip("'\"").strip()
        out[key] = value
    return out


@dataclass(frozen=True, slots=True)
class FormConfig:
    """Immutable configuration parsed from {% form %} tag arguments."""

    action_name: str
    html_attrs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_tag_args(cls, args: dict[str, str]) -> FormConfig:
        """Build FormConfig from parsed tag arguments dict."""
        action_name = args.get("@action") or args.get("action")
        if not action_name:
            msg = "{% form %} tag requires @action='action_name'"
            raise template.TemplateSyntaxError(msg)

        html_attrs = tuple(
            (k, v)
            for k, v in args.items()
            if not k.startswith("@") and k not in _RESERVED_KEYS
        )

        return cls(action_name=action_name, html_attrs=html_attrs)


@dataclass(slots=True)
class FormAttrsBuilder:
    """Builds form tag attributes: action URL, method, custom attrs."""

    action_url: str = ""
    html_attrs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_config(cls, config: FormConfig) -> FormAttrsBuilder:
        """Create builder from FormConfig, resolving action URL."""
        try:
            action_url = form_action_manager.get_action_url(config.action_name)
        except KeyError:
            action_url = ""

        return cls(action_url=action_url, html_attrs=config.html_attrs)

    def build_opening_tag(self) -> str:
        """Build <form ...> opening tag with all attributes."""
        parts = ['<form action="{}" method="post"']
        values: list[str] = [escape(self.action_url)]

        for name, value in self.html_attrs:
            parts.append(' {}="{}"')
            values.extend([escape(name), escape(str(value))])

        parts.append(">")
        return format_html("".join(parts), *values)


@register.tag(name="form")
def do_form(parser: template.base.Parser, token: template.base.Token) -> FormNode:
    """Block tag for {% form %} with @action."""
    bits = token.split_contents()
    if len(bits) < MIN_FORM_TAG_BITS:
        msg = f"{bits[0]!r} tag requires at least @action='...'"
        raise template.TemplateSyntaxError(msg) from None

    args = _parse_form_tag_args(" ".join(bits[1:]))
    config = FormConfig.from_tag_args(args)

    nodelist = parser.parse(("endform",))
    parser.delete_first_token()

    return FormNode(config=config, nodelist=nodelist)


class FormNode(template.Node):
    """Renders <form> with action URL, method="post", csrf_token."""

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

    def _build_hidden_inputs(self, request: HttpRequest) -> str:
        """Build CSRF hidden input and URL parameter inputs."""
        inputs = [
            format_html(
                '<input type="hidden" name="csrfmiddlewaretoken" value="{}">',
                get_token(request),
            )
        ]

        # Add hidden inputs for URL parameters (from file routing)
        # These will be passed to form handlers via POST data
        if request.resolver_match and request.resolver_match.kwargs:
            for key, value in request.resolver_match.kwargs.items():
                # Skip uid parameter used by form action system
                if key != "uid":
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
        builder = FormAttrsBuilder.from_config(self.config)

        # Get form from context by action name
        form_obj = context.get(self.config.action_name)
        if form_obj and hasattr(form_obj, "form"):
            form_instance = form_obj.form
        else:
            form_instance = None

        opening_tag = builder.build_opening_tag()
        hidden_inputs = self._build_hidden_inputs(request)

        # Push form as local variable (only available inside {% form %} tag)
        with context.push(form=form_instance):
            content = self.nodelist.render(context)

        return f"{opening_tag}\n{hidden_inputs}\n{content}\n</form>"
