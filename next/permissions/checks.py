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
from next.forms.checks.sources import iter_registered_actions
from next.pages.checks.access import access_in_use, load_declarations
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
    """Report a permission whose predicate cannot call its function (`next.E159`)."""
    return [
        Error(
            f"The permission {name!r} is checked by {rule.name!r}, which {problem}, so "
            "the predicate cannot call it.",
            hint=(
                "A predicate takes (), (user) or (user, obj), at most two positional "
                "parameters, with no *args and no required keyword-only parameter. "
                "**kwargs is allowed."
            ),
            obj=name,
            id="next.E159",
        )
        for name, rule in permission_manager.registry.items()
        if (problem := signature_problem(rule.test)) is not None
    ]


@register(NEXT)
def check_permission_backend(*args, **kwargs) -> list[CheckMessage]:
    """Report registered permissions no configured backend answers (`next.E160`)."""
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
            id="next.E160",
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


type _Place = tuple[str, str]
"""Where a permission name is written, as a message names it and as its `obj`."""


def _template_places() -> dict[tuple[str, _Place], None]:
    """Return each literal `{% has_perm %}` name with the first page or file using it.

    It names the first page reaching a literal name, else the component template.
    """
    found: dict[tuple[str, _Place], None] = {}
    seen: set[str] = set()
    where = ""

    def collect(nodelist: NodeList) -> bool:
        nodes = cast("list[HasPermNode]", nodelist.get_nodes_by_type(HasPermNode))
        for node in nodes:
            perm = node.literal_perm()
            if perm is not None and perm not in seen:
                seen.add(perm)
                found[perm, (f"{{% has_perm %}} in {where}", where)] = None
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
    return found


def _declared_places() -> dict[tuple[str, _Place], None]:
    """Return each permission a page, proxy or component declares, with its file."""
    load_declarations()
    return {
        (perm, (f"permission_required() in {file_path}", str(file_path))): None
        for file_path in declared_files()
        for requirement in declared(file_path).requirements
        for perm in requirement.perms
    }


def _guard_places() -> dict[tuple[str, _Place], None]:
    """Return each permission the guard of a form action names."""
    return {
        (perm, (f"The guard of the form action {meta.get('name')!r}", "")): None
        for meta in iter_registered_actions()
        if (guard := meta.get("guard")) is not None
        for perm in guard.permissions
    }


@register(Tags.templates, NEXT)
def check_permissions_known(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a permission name nothing grants, wherever written (`next.W127`).

    An action guard is read only once the project declares access, as it predates it.
    """
    if "django.contrib.auth" not in settings.INSTALLED_APPS:
        return []
    declared_places = _declared_places()
    guard_places = _guard_places() if access_in_use() else {}
    places = {**guard_places, **_template_places(), **declared_places}
    return [
        DjangoWarning(
            f"{where} names the permission {perm!r}, which no installed model "
            "defines and no add_perm() registers, so every user but a superuser "
            "is refused it.",
            hint=(
                "Fix the name, or register the permission with "
                "next.permissions.add_perm()."
            ),
            obj=obj or settings,
            id="next.W127",
        )
        for perm, (where, obj) in places
        if not permission_manager.known(perm)
    ]


__all__ = [
    "check_permission_backend",
    "check_permission_predicates",
    "check_permissions_known",
]
