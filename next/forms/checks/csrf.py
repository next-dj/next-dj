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

    A form whose action is not a literal, or names no registered action, may.
    """
    name = _literal_action(node)
    meta = (
        None
        if name is None
        else form_action_manager.get_action_meta(name, page_path=str(page_path))
    )
    return meta is None or not meta.get("requires_runtime", False)


def _forms(template: Template) -> list[FormNode]:
    """Return every `{% form %}` node of a composed page template."""
    return cast("list[FormNode]", template.nodelist.get_nodes_by_type(FormNode))


def _eager_csrf(
    page_path: Path, template: Template, forms: list[FormNode]
) -> CheckMessage | None:
    """Return `next.W112` for a shared page whose HTML carries the CSRF token."""
    if csrf_delivery() is not CsrfDelivery.EAGER:
        return None
    if not forms and not renders_runtime(template):
        return None
    return shared_warning(
        page_path,
        "CSRF_DELIVERY is 'eager' and it renders a {% form %} or the runtime, so "
        "every response sets the CSRF cookie and is sent with Cache-Control: "
        "private. Set CSRF_DELIVERY to 'auto'.",
        "next.W112",
    )


_BARE_FORM: Final = (
    "its {% form %} carries no CSRF field, so a browser without JavaScript gets "
    "403 on submit."
)
_REQUIRES_RUNTIME: Final = (
    "declare requires_runtime on the action that posts only through the runtime."
)


def _bare_forms(page_path: Path, forms: list[FormNode]) -> bool:
    """Whether a `{% form %}` of the page may post without the runtime."""
    return any(_posts_bare(node, page_path) for node in forms)


def _deferred_form(page_path: Path, forms: list[FormNode]) -> CheckMessage | None:
    """Return `next.W115` for a shared page whose forms post without a token field."""
    if csrf_delivery() is CsrfDelivery.EAGER or not _bare_forms(page_path, forms):
        return None
    return shared_warning(
        page_path,
        f"{_BARE_FORM} Keep the page private if it must work without the runtime, "
        f"or {_REQUIRES_RUNTIME}",
        "next.W115",
    )


def _lazy_private_forms(shared: set[Path]) -> list[CheckMessage]:
    """Return `next.W115` for every private page `CSRF_DELIVERY="lazy"` strips."""
    if csrf_delivery() is not CsrfDelivery.LAZY:
        return []
    return [
        DjangoWarning(
            f"{page_path} renders under CSRF_DELIVERY 'lazy', and {_BARE_FORM} Set "
            f"CSRF_DELIVERY to 'auto' to keep the token on private pages, or "
            f"{_REQUIRES_RUNTIME}",
            obj=str(page_path),
            id="next.W115",
        )
        for page_path, template in iter_composed_pages()
        if page_path not in shared and _bare_forms(page_path, _forms(template))
    ]


@register(Tags.templates, NEXT)
def check_shared_page_forms(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a page whose forms break the CSRF token delivery.

    A page a CDN may store is checked under every mode, a private one under `"lazy"`.
    """
    warnings: list[CheckMessage] = []
    shared: set[Path] = set()
    for page_path, template in shared_pages():
        shared.add(page_path)
        forms = _forms(template)
        found = (
            _eager_csrf(page_path, template, forms),
            _deferred_form(page_path, forms),
        )
        warnings.extend(warning for warning in found if warning is not None)
    warnings.extend(_lazy_private_forms(shared))
    return warnings


__all__ = ["check_shared_page_forms"]
