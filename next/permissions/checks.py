"""System checks for the object permissions and the `{% has_perm %}` tags."""

from pathlib import Path
from typing import Final, cast

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template import Template, TemplateSyntaxError
from django.template.base import NodeList

from next.checks import NEXT
from next.components.sources import get_components_manager
from next.conf import next_framework_settings
from next.pages.checks.access import tree_pages
from next.pages.checks.composed import iter_composed_pages
from next.pages.checks.metadata.templates import TemplateSearch
from next.pages.paths import page_template_path

from .access import declared, declared_files
from .backends import ObjectPermissionBackend
from .manager import listed_backends, permission_manager
from .markers import signature_problem
from .nodes import HasPermNode


_BACKEND: Final = (
    f"{ObjectPermissionBackend.__module__}.{ObjectPermissionBackend.__qualname__}"
)


@register(NEXT)
def check_permission_predicates(*args, **kwargs) -> list[CheckMessage]:
    """Report a predicate whose signature a leaf cannot call (`next.E161`)."""
    return [
        Error(
            f"The permission {name!r} is checked by {leaf_name!r}, which {problem}, so "
            "the predicate cannot call it.",
            hint=(
                "A predicate takes (), (user) or (user, obj), at most two positional "
                "parameters, with no *args and no required keyword-only parameter. "
                "**kwargs is allowed."
            ),
            obj=name,
            id="next.E161",
        )
        for name, rule in permission_manager.registry.items()
        for leaf_name, test in rule.tests()
        if (problem := signature_problem(test)) is not None
    ]


@register(NEXT)
def check_permission_backend(*args, **kwargs) -> list[CheckMessage]:
    """Report registered permissions no configured backend answers (`next.E162`)."""
    if not permission_manager.registry or _BACKEND in listed_backends():
        return []
    return [
        Error(
            "Object permissions are registered with next.permissions, but "
            "AUTHENTICATION_BACKENDS lists no ObjectPermissionBackend, so "
            "user.has_perm() never consults their predicates. Add "
            '"next.permissions.ObjectPermissionBackend" next to '
            '"django.contrib.auth.backends.ModelBackend".',
            hint=(
                "With more than one backend listed, login() needs the one to use, "
                'as in login(request, user, backend="django.contrib.auth.backends.'
                'ModelBackend"). A signup view calling login() without it raises.'
            ),
            obj=settings,
            id="next.E162",
        )
    ]


def _component_sources() -> list[tuple[Path, str]]:
    """Return the path and text of every component template, each file once."""
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return []
    manager = get_components_manager()
    found: dict[Path, str] = {}
    for backend in manager.backends:
        for info in backend.iter_components():
            source = manager.template_loader.load_source(info)
            if source is not None:
                found.setdefault(source.path, source.text)
    return list(found.items())


@register(Tags.templates, NEXT)
def check_has_perm_names(*args, **kwargs) -> list[CheckMessage]:
    """Warn about `{% has_perm %}` naming a permission nothing grants (`next.W130`).

    It names the first page reaching a literal name, else the component template.
    """
    unknown: dict[str, str] = {}
    where = ""

    def collect(nodelist: NodeList) -> bool:
        nodes = cast("list[HasPermNode]", nodelist.get_nodes_by_type(HasPermNode))
        for node in nodes:
            perm = node.literal_perm()
            if perm is not None and not permission_manager.known(perm):
                unknown.setdefault(perm, where)
        return False

    search = TemplateSearch(collect)
    for page_path, template in iter_composed_pages():
        where = str(page_path)
        search.reaches(template.nodelist, page_template_path(page_path))
    for path, text in _component_sources():
        if path in search.memo:
            continue
        try:
            compiled = Template(text)
        except TemplateSyntaxError:
            continue
        where = str(path)
        search.reaches(compiled.nodelist, path)
    return [
        DjangoWarning(
            f"{{% has_perm {perm!r} %}} in {first} names a permission no installed "
            "model defines and no add_perm() registers, so the tag renders its else "
            "branch for every user but a superuser. Fix the name, or register the "
            "permission with next.permissions.add_perm().",
            obj=first,
            id="next.W130",
        )
        for perm, first in unknown.items()
    ]


def _load_declarations() -> None:
    """Run every page, `proxy.py` and `component.py`, so each declaration is in."""
    tree_pages()
    if not isinstance(next_framework_settings.COMPONENT_BACKENDS, list):
        return
    for backend in get_components_manager().backends:
        backend.import_component_modules()


@register(NEXT)
def check_declared_permissions_known(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a declared permission nothing grants (`next.W131`).

    A proxy, a page and a component bind it the same way, so each file is named.
    """
    if "django.contrib.auth" not in settings.INSTALLED_APPS:
        return []
    _load_declarations()
    return [
        DjangoWarning(
            f"permission_required({perm!r}) in {file_path} names a permission no "
            "installed model defines and no add_perm() registers, so every user but "
            "a superuser is refused. Fix the name, or register the permission with "
            "next.permissions.add_perm().",
            obj=str(file_path),
            id="next.W131",
        )
        for file_path in declared_files()
        for requirement in declared(file_path).requirements
        for perm in requirement.perms
        if not permission_manager.known(perm)
    ]


__all__ = [
    "check_declared_permissions_known",
    "check_has_perm_names",
    "check_permission_backend",
    "check_permission_predicates",
]
