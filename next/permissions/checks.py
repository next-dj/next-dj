"""System checks for the object permissions and the `{% has_perm %}` tags."""

from collections.abc import Iterator
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
from next.components.sources import components_listed, get_components_manager
from next.conf import next_framework_settings
from next.conf.defaults import USER_SETTING
from next.forms.checks.sources import iter_registered_actions
from next.pages.checks.access import access_in_use, load_declarations
from next.pages.checks.composed import iter_composed_pages
from next.pages.checks.metadata.templates import TemplateSearch
from next.pages.paths import page_template_path
from next.utils import middleware_listed

from .access import auth_installed, declared, declared_files, login_middleware
from .backends import ObjectPermissionBackend
from .manager import listed_backends, permission_manager
from .markers import signature_problem
from .nodes import HasPermNode


_AUTH_MIDDLEWARE: Final = "django.contrib.auth.middleware.AuthenticationMiddleware"

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
    if not components_listed():
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


def _searched_templates(
    search: TemplateSearch,
) -> Iterator[tuple[Path, NodeList, Path]]:
    """Yield each page composition, then each component template no page reached."""
    for page_path, template in iter_composed_pages():
        yield page_path, template.nodelist, page_template_path(page_path)
    for path, text in _component_sources():
        if path in search.memo:
            continue
        try:
            compiled = Template(text)
        except TemplateSyntaxError:
            continue
        yield path, compiled.nodelist, path


def _template_places() -> dict[tuple[str, _Place], None]:
    """Return each literal `{% has_perm %}` name with the first place using it."""
    perms: list[str] = []

    def collect(nodelist: NodeList) -> bool:
        nodes = cast("list[HasPermNode]", nodelist.get_nodes_by_type(HasPermNode))
        literals = (node.literal_perm() for node in nodes)
        perms.extend(perm for perm in literals if perm is not None)
        return False

    search = TemplateSearch(collect)
    first: dict[str, str] = {}
    for place, nodelist, template_path in _searched_templates(search):
        search.reaches(nodelist, template_path)
        for perm in perms:
            first.setdefault(perm, str(place))
        perms.clear()
    return {
        (perm, (f"{{% has_perm %}} in {where}", where)): None
        for perm, where in first.items()
    }


def _declared_places() -> dict[tuple[str, _Place], None]:
    """Return each permission a page or a component declares, with its file."""
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
    if not auth_installed():
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


@register(NEXT)
def check_login_required_setting(*args, **kwargs) -> list[CheckMessage]:
    """Warn about `LOGIN_REQUIRED` set false beside the login middleware (`next.W130`).

    The middleware asks the login anyway, so the setting turns nothing off.
    """
    raw = getattr(settings, USER_SETTING, None)
    if not isinstance(raw, dict) or raw.get("LOGIN_REQUIRED", True):
        return []
    entry = login_middleware()
    if entry is None:
        return []
    return [
        DjangoWarning(
            f'NEXT_FRAMEWORK["LOGIN_REQUIRED"] is False, yet MIDDLEWARE lists {entry}, '
            "which asks a login of every page and action that declares no access, "
            "so the setting turns nothing off.",
            hint=(
                "Drop the key or set it to True, or remove the middleware and declare "
                "login_required() where a login is meant."
            ),
            obj=settings,
            id="next.W130",
        )
    ]


def _missing_user() -> str | None:
    """Return what keeps a signed-in user off every request, `None` when nothing."""
    if not auth_installed():
        return "django.contrib.auth is not in INSTALLED_APPS"
    if not middleware_listed(settings.MIDDLEWARE, _AUTH_MIDDLEWARE):
        return f"MIDDLEWARE lists no {_AUTH_MIDDLEWARE}"
    return None


@register(NEXT)
def check_login_required_auth(*args, **kwargs) -> list[CheckMessage]:
    """Warn about `LOGIN_REQUIRED` on without auth or its middleware (`next.W131`).

    No request then carries a signed-in user, so the default login refuses everyone.
    """
    if not next_framework_settings.LOGIN_REQUIRED:
        return []
    missing = _missing_user()
    if missing is None:
        return []
    return [
        DjangoWarning(
            f'NEXT_FRAMEWORK["LOGIN_REQUIRED"] is True, yet {missing}, so no request '
            "carries a signed-in user and every page and action that declares no "
            "access sends each visitor to the login.",
            hint=(
                "Install django.contrib.auth with its AuthenticationMiddleware, or "
                "drop the key and declare login_required() where a login is meant."
            ),
            obj=settings,
            id="next.W131",
        )
    ]


__all__ = [
    "check_login_required_auth",
    "check_login_required_setting",
    "check_permission_backend",
    "check_permission_predicates",
    "check_permissions_known",
]
