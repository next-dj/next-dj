"""HTML rendering for validation-error responses."""

import types
from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.deps.cache import render_dep_cache
from next.diagnostics import status_kept
from next.pages import page
from next.pages.boundaries import Caught, boundary_scope

from .origin import resolve_origin


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest

    from .backends import FormActionBackend
    from .wizard import FormWizard


def _form_fallback_html(form: "BaseForm | BaseFormSet | None") -> str:
    """Render a bare form through its pinned div renderer."""
    if form is None:
        return ""
    return str(form.render(form.template_name_p))


@dataclass(frozen=True, slots=True)
class ErrorRenderParams:
    """Bundle of failed-submission params for the validation-error re-render."""

    action_name: str
    form: "BaseForm | BaseFormSet | None"
    url_kwargs: dict[str, object]
    overrides: dict[str, object] | None = None


def render_form_page_with_errors(
    backend: "FormActionBackend",
    request: "HttpRequest",
    params: ErrorRenderParams,
    page_file_path: "Path",
) -> str:
    """Render the page template for `page_file_path` with a bound form in context.

    Dispatch authorized the page first, so the re-render never decides who may see what.
    A failure a page boundary contains renders its error UI, at the form flow's status.
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

    chain = page.composed_chain(file_path)
    with status_kept(), boundary_scope(chain):
        built = page.guarded_context(file_path, request, params.url_kwargs, chain=chain)
        if isinstance(built, Caught):
            return page.render_caught(
                file_path,
                built,
                request,
                params.url_kwargs,
                dep_cache=render_dep_cache(request),
                chain=chain,
            )
        _bind_form(built, request, params, meta.get("wizard_class"))
        rendered, _collector = page.render_with_static_assets(
            file_path, template, built, request=request
        )
    return rendered


def _bind_form(
    context_data: dict[str, object],
    request: "HttpRequest",
    params: ErrorRenderParams,
    wizard_class: "type[FormWizard] | None",
) -> None:
    """Put the bound form, its wizard and the overrides into `context_data`."""
    form = params.form
    url_kwargs = params.url_kwargs
    overrides = params.overrides or {}
    if form is not None:
        namespace = types.SimpleNamespace(form=form)
        # An overridden wizard is bound to its step, a rebuild would use the old origin.
        if wizard_class is not None and "wizard" not in overrides:
            origin_match = resolve_origin(request)
            origin = origin_match.path if origin_match is not None else ""
            wizard = wizard_class(
                request=request, url_kwargs=url_kwargs, base_path=origin
            )
            namespace.wizard = wizard
            context_data["wizard"] = wizard
        context_data[params.action_name] = namespace
        context_data["form"] = form
    context_data.update(overrides)


__all__ = ["ErrorRenderParams", "render_form_page_with_errors"]
