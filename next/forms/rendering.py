"""HTML rendering for validation-error responses."""

import types
from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.pages import page

from .origin import resolve_origin


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest

    from .backends import FormActionBackend


def _form_fallback_html(form: "BaseForm | BaseFormSet | None") -> str:
    """Render a bare form through its pinned div renderer."""
    if form is None:
        return ""
    return str(form.render(form.template_name_p))


@dataclass(frozen=True, slots=True)
class _ErrorRenderParams:
    """Bundle of failed-submission params for the validation-error re-render."""

    action_name: str
    form: "BaseForm | BaseFormSet | None"
    url_kwargs: dict[str, object]
    overrides: dict[str, object] | None = None


def render_form_page_with_errors(
    backend: "FormActionBackend",
    request: "HttpRequest",
    params: _ErrorRenderParams,
    page_file_path: "Path",
) -> str:
    """Render the page template for `page_file_path` with a bound form in context.

    The rendered HTML flows through `Page.render_with_static_assets`
    so co-located CSS and JS land in the response and any
    request-aware backend (such as a per-tenant URL prefix) sees the
    same `request` it does on the canonical render path.
    """
    file_path = page_file_path
    action_name = params.action_name
    form = params.form
    meta = backend.get_meta(action_name, str(file_path))
    if not meta:
        return _form_fallback_html(form)

    template = page.composed_template_for(file_path)
    if not template.source:
        return _form_fallback_html(form)

    url_kwargs = params.url_kwargs
    overrides = params.overrides or {}

    # Pinned for mypy, which cannot rule out the untyped splat reaching it.
    context_data = page.build_render_context(
        file_path, request, _requested_zones=None, **url_kwargs
    )
    if form is not None:
        namespace = types.SimpleNamespace(form=form)
        wizard_class = meta.get("wizard_class")
        # A wizard in the overrides is already bound to the right step, a
        # rebuild here would bind the posted previous-step origin instead.
        if wizard_class is not None and "wizard" not in overrides:
            origin_match = resolve_origin(request)
            origin = origin_match.origin if origin_match is not None else ""
            wizard = wizard_class(
                request=request, url_kwargs=url_kwargs, base_path=origin
            )
            namespace.wizard = wizard
            context_data["wizard"] = wizard
        context_data[action_name] = namespace
        context_data["form"] = form
    context_data.update(overrides)

    rendered, _collector = page.render_with_static_assets(
        file_path, template, context_data, request=request
    )
    return rendered


__all__ = ["_ErrorRenderParams", "render_form_page_with_errors"]
