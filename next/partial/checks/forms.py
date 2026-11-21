"""System checks where a form action meets partial rendering."""

import ast
import inspect
import textwrap
from typing import Final

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register

from next.checks import NEXT
from next.forms.backends import FormActionBackend
from next.forms.manager import form_action_manager
from next.pages.checks.composed import iter_composed_pages
from next.partial.keys import FORM_KEY_ATTR, FORM_ZONE_ATTR

from .backends import partial_backends_active
from .codes import W_FORM_BACKEND_NOT_AWARE, W_FORM_IN_FOR_NO_KEY
from .nodes import forms_in_loop


_HOOK: Final = "shape_response"
_SHAPER_ATTR: Final = "shaper"


@register(Tags.templates, NEXT)
def check_repeated_form_has_key(*args: object, **kwargs: object) -> list[CheckMessage]:
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


def _parsed(function: object) -> ast.Module | None:
    """Parse the source of one hook, `None` when it is no readable function."""
    if not inspect.isfunction(function):
        return None
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(function))))
    except (OSError, SyntaxError):
        return None


def _is_shaper_read(node: ast.AST, receiver: str) -> bool:
    """Return whether one node reads `<receiver>.shaper` or `getattr` of it."""
    match node:
        case ast.Attribute(value=ast.Name(id=owner), attr=attribute):
            return owner == receiver and attribute == _SHAPER_ATTR
        case ast.Call(
            func=ast.Name(id="getattr"),
            args=[ast.Name(id=owner), ast.Constant(value=attribute), *_],
        ):
            return owner == receiver and attribute == _SHAPER_ATTR
    return False


def _reads_shaper(tree: ast.Module, receiver: str | None) -> bool:
    """Return whether the parsed hook reads the shaper off its instance."""
    if receiver is None:
        return False
    return any(_is_shaper_read(node, receiver) for node in ast.walk(tree))


def _defers_to_super(tree: ast.Module) -> bool:
    """Return whether the parsed hook calls `super().shape_response`."""
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == _HOOK
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "super"
        for node in ast.walk(tree)
    )


def _hook_owner(node: ast.AST) -> str | None:
    """Return the class name a `<Class>.shape_response` access names, else `None`."""
    match node:
        case ast.Attribute(value=ast.Name(id=name), attr=attribute) if (
            attribute == _HOOK
        ):
            return name
        case ast.Attribute(value=ast.Attribute(attr=name), attr=attribute) if (
            attribute == _HOOK
        ):
            return name
    return None


def _named_bases(tree: ast.Module, klass: type) -> list[type]:
    """Return the ancestors of `klass` whose `shape_response` the hook names outright.

    `FormActionBackend.shape_response(self, ...)` defers the way `super()` does.
    """
    ancestors = {base.__name__: base for base in klass.__mro__[1:]}
    owners = (_hook_owner(node) for node in ast.walk(tree))
    return [ancestors[name] for name in owners if name in ancestors]


def _hook_function(override: object) -> tuple[object, str | None]:
    """Return the function behind a hook and the name its instance arrives under.

    A static or class method sees no instance, so it has no shaper to read.
    """
    if isinstance(override, staticmethod | classmethod):
        return override.__func__, None
    if not inspect.isfunction(override):
        return override, None
    return override, next(iter(inspect.signature(override).parameters), None)


def _consults_shaper(backend_class: type) -> bool:
    """Return whether `shape_response` of the class reaches the instance shaper.

    A hook calling `super().shape_response` or a named base hands the answer on.
    """
    mro = backend_class.__mro__
    stop = mro.index(FormActionBackend) if FormActionBackend in mro else len(mro)
    for klass in mro[:stop]:
        override = vars(klass).get(_HOOK)
        if override is None:
            continue
        function, receiver = _hook_function(override)
        tree = _parsed(function)
        if tree is None or _reads_shaper(tree, receiver):
            return True
        if any(_consults_shaper(base) for base in _named_bases(tree, klass)):
            return True
        if not _defers_to_super(tree):
            return False
    return True


@register(NEXT)
def check_form_backend_partial_aware(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Warn when a form backend shapes responses without its shaper (`next.W068`).

    An override that never consults `self.shaper` answers a partial request in full.
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
        if _consults_shaper(backend_class):
            continue
        messages.append(
            DjangoWarning(
                f"Form action backend {backend_class.__name__!r} overrides "
                "shape_response without consulting self.shaper. Call "
                "super().shape_response or self.shaper.shape in the override, or "
                "a partial request receives a full page instead of a patch "
                "envelope.",
                id=W_FORM_BACKEND_NOT_AWARE,
            )
        )
    return messages


__all__ = ["check_form_backend_partial_aware", "check_repeated_form_has_key"]
