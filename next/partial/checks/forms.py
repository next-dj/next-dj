"""System checks where a form action meets partial rendering.

The ids are `next.W070` for a looped `{% form %}` with no identity and `next.W068` for
a form action backend that shapes its own response without the partial branch.
"""

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register

from next.checks import NEXT
from next.forms.backends import FormActionBackend
from next.forms.manager import form_action_manager
from next.partial.keys import FORM_KEY_ATTR, FORM_ZONE_ATTR

from .backends import partial_backends_active
from .codes import W_FORM_BACKEND_NOT_AWARE, W_FORM_IN_FOR_NO_KEY
from .nodes import forms_in_loop
from .pages import iter_composed_pages


@register(Tags.templates, NEXT)
def check_repeated_form_has_key(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a looped `{% form %}` has no key or zone (`next.W070`)."""
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        for node in forms_in_loop(template.nodelist):
            if node.has_partial_attr(FORM_ZONE_ATTR):
                continue
            if node.has_partial_attr(FORM_KEY_ATTR):
                continue
            messages.append(
                DjangoWarning(
                    f"Form {node.action_expr} in {page_path} renders inside a "
                    "{% for %} loop without a key= or a zone=. Every iteration "
                    "shares one action uid, so a partial morph cannot tell the "
                    "instances apart and re-renders the wrong one. Add key= with "
                    "a stable per-row value, or a zone= that wraps the list.",
                    obj=str(page_path),
                    id=W_FORM_IN_FOR_NO_KEY,
                )
            )
    return messages


@register(NEXT)
def check_form_backend_partial_aware(*args, **kwargs) -> list[CheckMessage]:
    """Warn when partial rendering is on but a form backend is not aware (`next.W068`).

    A backend that overrides `shape_response` without the partial branch drops the patch
    envelope and serves a full page, so only an override is warned about.
    """
    if not partial_backends_active():
        return []
    messages: list[CheckMessage] = []
    seen: set[type] = set()
    for backend in form_action_manager.backends:
        backend_class = type(backend)
        if backend_class in seen:
            continue
        seen.add(backend_class)
        if backend_class.shape_response is FormActionBackend.shape_response:
            continue
        messages.append(
            DjangoWarning(
                f"Form action backend {backend_class.__name__!r} overrides "
                "shape_response, but PARTIAL_BACKENDS is configured. Route "
                "partial requests through next.partial.shape_partial in the "
                "override, or the runtime receives a full page instead of a "
                "patch envelope.",
                id=W_FORM_BACKEND_NOT_AWARE,
            )
        )
    return messages


__all__ = ["check_form_backend_partial_aware", "check_repeated_form_has_key"]
