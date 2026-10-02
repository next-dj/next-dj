"""System checks for how a page delivers the CSRF token to its forms."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register

from next.checks import NEXT
from next.csrf import CsrfDelivery, csrf_delivery
from next.forms.manager import form_action_manager
from next.forms.nodes import FormNode
from next.pages.checks.composed import iter_composed_pages
from next.pages.checks.responses import renders_runtime, shared_pages, shared_warning


if TYPE_CHECKING:
    from pathlib import Path

    from django.template.base import Template


def _literal_action(node: FormNode) -> str | None:
    """Return the action name a `{% form %}` spells as a quoted literal."""
    expression = node.action_expr
    name = expression.var
    return name if isinstance(name, str) and not expression.filters else None


def _posts_bare(node: FormNode, page_path: Path) -> bool:
    """Whether a `{% form %}` may post without the runtime, so without a token field.

    A form whose action is no literal, or names no registered action, may.
    """
    name = _literal_action(node)
    meta = (
        None
        if name is None
        else form_action_manager.get_action_meta(name, page_path=str(page_path))
    )
    return meta is None or not meta.get("requires_runtime", False)


def _eager_csrf(page_path: Path, template: Template) -> CheckMessage | None:
    """Return `next.W121` for a shared page whose HTML carries the CSRF token."""
    if csrf_delivery() is not CsrfDelivery.EAGER:
        return None
    forms = template.nodelist.get_nodes_by_type(FormNode)
    if not forms and not renders_runtime(template):
        return None
    return shared_warning(
        page_path,
        "CSRF_DELIVERY is 'eager' and it renders a {% form %} or the runtime, so "
        "every response sets the CSRF cookie and goes out private. Set "
        "CSRF_DELIVERY to 'auto'.",
        "next.W121",
    )


_BARE_FORM: Final = (
    "its {% form %} carries no CSRF field, so a browser without JavaScript gets "
    "403 on submit."
)
_REQUIRES_RUNTIME: Final = (
    "declare requires_runtime on the action that posts only through the runtime."
)


def _bare_forms(page_path: Path, template: Template) -> bool:
    """Whether a `{% form %}` of the page may post without the runtime."""
    nodes = cast("list[FormNode]", template.nodelist.get_nodes_by_type(FormNode))
    return any(_posts_bare(node, page_path) for node in nodes)


def _deferred_form(page_path: Path, template: Template) -> CheckMessage | None:
    """Return `next.W124` for a shared page whose forms post without a token field."""
    if csrf_delivery() is CsrfDelivery.EAGER or not _bare_forms(page_path, template):
        return None
    return shared_warning(
        page_path,
        f"{_BARE_FORM} Keep the page private if it must work without the runtime, "
        f"or {_REQUIRES_RUNTIME}",
        "next.W124",
    )


def _lazy_private_forms(shared: set[Path]) -> list[CheckMessage]:
    """Return `next.W124` for every private page `CSRF_DELIVERY="lazy"` strips."""
    if csrf_delivery() is not CsrfDelivery.LAZY:
        return []
    return [
        DjangoWarning(
            f"{page_path} renders under CSRF_DELIVERY 'lazy', and {_BARE_FORM} Set "
            f"CSRF_DELIVERY to 'auto' to keep the token on private pages, or "
            f"{_REQUIRES_RUNTIME}",
            obj=str(page_path),
            id="next.W124",
        )
        for page_path, template in iter_composed_pages()
        if page_path not in shared and _bare_forms(page_path, template)
    ]


@register(Tags.templates, NEXT)
def check_shared_page_forms(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a page whose forms break the CSRF token delivery.

    A page a CDN may hold is checked under every mode, a private one under `"lazy"`.
    """
    warnings: list[CheckMessage] = []
    shared: set[Path] = set()
    for page_path, template in shared_pages():
        shared.add(page_path)
        found = (_eager_csrf(page_path, template), _deferred_form(page_path, template))
        warnings.extend(warning for warning in found if warning is not None)
    warnings.extend(_lazy_private_forms(shared))
    return warnings


__all__ = ["check_shared_page_forms"]
