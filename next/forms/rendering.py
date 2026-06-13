"""HTML rendering for validation-error responses."""

import types
from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.pages import page

from .origin import _resolve_origin


if TYPE_CHECKING:
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest

    from .backends import FormActionBackend


def _form_fallback_html(form: "django_forms.Form | None") -> str:
    """Render a bare form without tripping the Django 4.2 default-renderer warning."""
    if form is None:
        return ""
    return str(form.render(form.template_name_p))


@dataclass(frozen=True, slots=True)
class _ErrorRenderParams:
    """Bundle of failed-submission params for the validation-error re-render."""

    action_name: str
    form: "django_forms.Form | None"
    url_kwargs: dict[str, object]


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

    context_data = page.build_render_context(file_path, request, **url_kwargs)
    if form is not None:
        namespace = types.SimpleNamespace(form=form)
        wizard_class = meta.get("wizard_class")
        if wizard_class is not None:
            origin_match = _resolve_origin(request)
            origin = origin_match.origin if origin_match is not None else ""
            wizard = wizard_class(
                request=request, url_kwargs=url_kwargs, base_path=origin
            )
            namespace.wizard = wizard
            context_data["wizard"] = wizard
        context_data[action_name] = namespace
        context_data["form"] = form

    rendered, _collector = page.render_with_static_assets(
        file_path,
        template,
        context_data,
        request=request,
    )
    return rendered


__all__ = ["_ErrorRenderParams", "render_form_page_with_errors"]
