"""HTML rendering for validation-error responses."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

from django.template import Context as DjangoTemplateContext, Template

from next.pages import page
from next.pages.loaders import _load_python_module_memo

from .dispatch import _url_kwargs_from_post


if TYPE_CHECKING:
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest

    from .backends import FormActionBackend


def render_form_page_with_errors(  # noqa: PLR0913
    backend: FormActionBackend,
    request: HttpRequest,
    action_name: str,
    form: django_forms.Form | None,
    template_fragment: str | None,
    page_file_path: Path,
) -> str:
    """Render the page template for `page_file_path` with a bound form in context."""
    del template_fragment

    meta = backend.get_meta(action_name)
    if not meta:
        return form.as_p() if form else ""

    file_path = page_file_path
    module = _load_python_module_memo(file_path)
    body = page._load_static_body(file_path, module)
    template_str = page._layout_manager._layout_loader.compose_body(body, file_path)
    if not template_str:
        return form.as_p() if form else ""

    url_kwargs = _url_kwargs_from_post(request)

    context_data = page.build_render_context(file_path, request, **url_kwargs)
    if form is not None:
        context_data[action_name] = types.SimpleNamespace(form=form)
        context_data["form"] = form

    return Template(template_str).render(DjangoTemplateContext(context_data))


__all__ = ["render_form_page_with_errors"]
