"""HTML rendering for validation-error responses."""

import types
from typing import TYPE_CHECKING

from next.pages import page
from next.pages.loaders import _load_python_module_memo

from ._request_utils import _url_kwargs_from_post
from .uid import _validated_origin_path


if TYPE_CHECKING:
    from pathlib import Path

    from django import forms as django_forms
    from django.http import HttpRequest

    from .backends import FormActionBackend


def render_form_page_with_errors(
    backend: "FormActionBackend",
    request: "HttpRequest",
    action_name: str,
    form: "django_forms.Form | None",
    page_file_path: "Path",
) -> str:
    """Render the page template for `page_file_path` with a bound form in context.

    The rendered HTML flows through `Page.render_with_static_assets`
    so co-located CSS and JS land in the response and any
    request-aware backend (such as a per-tenant URL prefix) sees the
    same `request` it does on the canonical render path.
    """
    file_path = page_file_path
    meta = backend.get_meta(action_name, page_path=str(file_path))
    if not meta:
        return form.as_p() if form else ""

    module = _load_python_module_memo(file_path)
    body = page._load_static_body(file_path, module)
    template_str = page._layout_manager._layout_loader.compose_body(body, file_path)
    if not template_str:
        return form.as_p() if form else ""

    url_kwargs = _url_kwargs_from_post(request)

    context_data = page.build_render_context(file_path, request, **url_kwargs)
    if form is not None:
        namespace = types.SimpleNamespace(form=form)
        wizard_class = meta.get("wizard_class")
        if wizard_class is not None:
            origin = (
                _validated_origin_path(request.POST.get("_next_form_origin"))
                or request.path
            )
            wizard = wizard_class(
                request=request, url_kwargs=url_kwargs, base_path=origin
            )
            namespace.wizard = wizard
            context_data["wizard"] = wizard
        context_data[action_name] = namespace
        context_data["form"] = form

    rendered, _collector = page.render_with_static_assets(
        file_path,
        template_str,
        context_data,
        request=request,
    )
    return rendered


__all__ = ["render_form_page_with_errors"]
