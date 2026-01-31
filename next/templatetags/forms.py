"""Template tag {% form %} for next-dj form actions.

Parses @action, @method, @action.then and other HTML attributes, inserts csrf_token,
and outputs a <form> with HTMX attributes when needed.
"""

import re

from django import template
from django.utils.html import escape, format_html

from next.forms import form_action_manager


register = template.Library()


def _parse_form_tag_args(contents: str) -> dict[str, str]:
    """Parse tag contents into key=value dict. Supports @action, @method, etc."""
    out: dict[str, str] = {}
    # Match key="value" or key='value' or key=value (no spaces in value)
    pattern = re.compile(
        r'(@?[\w.-]+)\s*=\s*([" \'])((?:\\.|(?!\2).)*?)\2|(@?[\w.-]+)\s*=\s*(\S+)'
    )
    for m in pattern.finditer(contents):
        if m.group(1) is not None:
            key = m.group(1).strip()
            value = m.group(3)
        else:
            key = m.group(4).strip()
            value = m.group(5).strip().strip("'\"").strip()
        out[key] = value
    return out


MIN_FORM_TAG_BITS = 2


@register.tag(name="form")
def do_form(parser: template.base.Parser, token: template.base.Token) -> "FormNode":
    """Block tag for {% form %} with @action and optional @action.then."""
    bits = token.split_contents()
    if len(bits) < MIN_FORM_TAG_BITS:
        msg = f"{bits[0]!r} tag requires at least @action='...'"
        raise template.TemplateSyntaxError(msg) from None
    rest = " ".join(bits[1:])
    args = _parse_form_tag_args(rest)
    action_name = args.get("@action") or args.get("action")
    if not action_name:
        msg = "{% form %} tag requires @action='action_name'"
        raise template.TemplateSyntaxError(msg)
    method = (args.get("@method") or args.get("method") or "post").lower()
    then = args.get("@action.then") or args.get("action.then") or "refresh-self"
    # Collect other HTML attributes (omit our special @ keys)
    html_attrs = {
        k: v
        for k, v in args.items()
        if not k.startswith("@") and k not in ("action", "method", "action.then")
    }
    nodelist = parser.parse(("endform",))
    parser.delete_first_token()
    return FormNode(
        action_name=action_name,
        method=method,
        then=then,
        html_attrs=html_attrs,
        nodelist=nodelist,
    )


class FormNode(template.Node):
    """Renders <form> with action URL, method, HTMX attributes, csrf_token."""

    def __init__(
        self,
        action_name: str,
        method: str,
        then: str,
        html_attrs: dict[str, str],
        nodelist: template.NodeList,
    ) -> None:
        """Store tag args and nodelist for render."""
        self.action_name = action_name
        self.method = method
        self.then = then
        self.html_attrs = html_attrs
        self.nodelist = nodelist

    def render(self, context: template.Context) -> str:
        """Render form tag with action URL, HTMX attrs, CSRF, and block content."""
        try:
            action_url = form_action_manager.get_action_url(self.action_name)
        except KeyError:
            action_url = ""
        fmt_parts = ['<form action="{}" method="{}"']
        args: list = [escape(action_url), escape(self.method)]
        if self.then == "refresh-self":
            fmt_parts.append(' hx-post="{}" hx-swap="outerHTML" hx-target="this"')
            args.append(escape(action_url))
        elif self.then == "redirect":
            fmt_parts.append(' hx-post="{}"')
            args.append(escape(action_url))
        for key, value in self.html_attrs.items():
            fmt_parts.append(' {}="{}"')
            args.append(escape(key))
            args.append(escape(str(value)))
        fmt_parts.append(">\n{}\n{}\n</form>")
        content = self.nodelist.render(context)
        from django.middleware.csrf import get_token

        req = context.get("request")
        token_val = context.get("csrf_token") if req else ""
        if not token_val and req:
            token_val = get_token(req)
        csrf_str = (
            format_html(
                '<input type="hidden" name="csrfmiddlewaretoken" value="{}">',
                token_val,
            )
            if token_val
            else ""
        )
        args.extend([csrf_str, content])
        return format_html("".join(fmt_parts), *args)
