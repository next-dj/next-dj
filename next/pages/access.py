"""The `proxy.py` declarations of a page tree and the access rule each page answers to.

A rule is read once per reload of a file in its chain, so a request reads one memo.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from django.http import Http404

from next.caches import PageCache
from next.conf import fail_loudly
from next.deps.objects import object_target, url_row
from next.introspect import registering_file
from next.permissions.access import (
    Declarations,
    answer,
    check,
    declarations,
    login_middleware_active,
    login_redirect,
    require_permission,
    signed_in,
)
from next.permissions.markers import chained
from next.utils import template_edits_watched

from .loaders import (
    PAGE_MODULE,
    AncestorStamps,
    has_load_errors,
    in_page_tree,
    last_load_error,
    module_generation,
    module_version,
    proxy_modules,
)


if TYPE_CHECKING:
    from pathlib import Path

    from django.db.models import Model
    from django.http import HttpRequest, HttpResponseRedirect

    from next.deps.objects import DObject
    from next.permissions.access import Lookup
    from next.permissions.markers import AccessObject, AccessRule


def _url_model(obj: object) -> type[Model] | None:
    """Return the model an `obj=DObject[Model]` names, refusing any other value."""
    if obj is None:
        return None
    target = object_target(obj)
    if target is None:
        msg = (
            "permission_required(obj=) takes DObject[Model], as obj=DObject[Book], "
            f"got {obj!r}. The row loads by the URL kwarg named after the model."
        )
        raise TypeError(msg)
    model, parent = target
    if parent is not None:
        msg = (
            f"permission_required(obj=DObject[{model.__name__}, {parent!r}]) takes "
            "no parent, since the check runs before any context gives one."
        )
        raise TypeError(msg)
    return model


class TreeDeclarations(Declarations):
    """The access a `page.py` or a `proxy.py` declares, each bound to its own file.

    A `proxy.py` binds its directory and below, a `page.py` its own page alone.
    """

    def permission_required(
        self, perm: str | tuple[str, ...], *, obj: type[DObject[Any]] | None = None
    ) -> None:
        """Require every permission in `perm`, checked on the row `obj` names.

        The row loads through `visible_to(scope)` by the URL kwarg named like the model.
        """
        require_permission(registering_file(), perm, _url_model(obj))

    def public(self) -> None:
        """Lift the requirements of every `proxy.py` above for what this file covers.

        A requirement of this file or of a `proxy.py` below still binds.
        """
        declarations.declare_public(registering_file())


class Proxy(TreeDeclarations):
    """The `proxy` a `proxy.py` declares the access of its directory and below with."""


@dataclass(frozen=True, slots=True)
class PageAccess:
    """The access rule of one page with the stamps of the files it was read from.

    `broken` names the first file of the chain whose latest load failed.
    """

    rule: AccessRule
    broken: Path | None
    ancestors: AncestorStamps
    version: int
    stamps: tuple[int | None, ...]


_PAGES: PageCache[Path, PageAccess] = PageCache()

type AccessStamp = tuple[int, int, int]


def access_stamp() -> AccessStamp | None:
    """Return a stamp that holds while no rule of any page can have changed.

    Watched template edits re-read the disk per request, so they leave no stamp.
    """
    if template_edits_watched():
        return None
    return module_version(), module_generation(), declarations.version


def _read(file_path: Path) -> PageAccess:
    """Load every file of the chain once and join what each declares, root first."""
    paths = (*proxy_modules(file_path), file_path)
    ancestors = AncestorStamps(
        paths=paths, version=module_version(), watched=template_edits_watched()
    )
    version = declarations.version
    ancestors, _modules = ancestors.loaded()
    broken = None
    if has_load_errors():
        broken = next(
            (path for path in paths if last_load_error(path) is not None), None
        )
    rule = chained(map(declarations.declared, paths))
    return PageAccess(rule, broken, ancestors, version, declarations.stamps(paths))


def _revalidated(held: PageAccess) -> PageAccess | None:
    """Return `held` while no file of its chain reloaded or redeclared, else `None`."""
    if held.broken is not None:
        return None
    ancestors = held.ancestors.revalidated()
    if ancestors is None:
        return None
    version = declarations.version
    if held.version != version:
        if declarations.stamps(held.ancestors.paths) != held.stamps:
            return None
        held = replace(held, version=version)
    if ancestors is not held.ancestors:
        held = replace(held, ancestors=ancestors)
    return held


def page_access(file_path: Path) -> PageAccess:
    """Return the rule of the page at `file_path`, its `proxy.py` chain and its own.

    A chain with a broken file is read again on every call, so a fix applies at once.
    """
    stored = _PAGES.get(file_path)
    held = None if stored is None else _revalidated(stored)
    if held is None:
        held = _read(file_path)
    if held is not stored:
        _PAGES[file_path] = held
    return held


def owner_access(file_path: Path) -> PageAccess | None:
    """Return the access over a file of a page tree, `None` for a file outside one.

    Any file answers with the rule of the page in its directory, a page or not.
    """
    directory = file_path.parent
    if not in_page_tree(directory):
        return None
    return page_access(directory / PAGE_MODULE)


def left_to_middleware(rule: AccessRule) -> bool:
    """Return whether a rule asks nothing and is not public, left to the middleware."""
    return not rule.requirements and not rule.public


def middleware_login(rule: AccessRule) -> bool:
    """Return whether `LoginRequiredMiddleware` asks a login of a rule asking none."""
    return left_to_middleware(rule) and login_middleware_active()


def anonymous_admitted(access: PageAccess) -> bool:
    """Return whether the rule `access` holds lets an anonymous request in."""
    rule = access.rule
    return not rule.requirements and not middleware_login(rule)


def refuse_broken(broken: Path) -> None:
    """Raise the import failure of `broken` when failing loudly, otherwise a 404."""
    error = last_load_error(broken)
    if error is not None and fail_loudly():
        raise error
    raise Http404


def url_lookup(
    request: HttpRequest | None,
    url_kwargs: dict[str, object],
    dep_cache: dict[str, Any],
) -> Lookup:
    """Return the lookup of the rows an `obj=` of a page or a `proxy.py` names.

    A row read here is the one a `DObject` of the same model reads, from `dep_cache`.
    """

    def lookup(target: AccessObject) -> object:
        if isinstance(target, str):
            msg = (
                f"A page checks a permission on the context {target!r}, which only "
                "a component names. Pass obj=DObject[Model] to "
                "page.permission_required() or proxy.permission_required()."
            )
            raise TypeError(msg)
        return url_row(target, request, url_kwargs, dep_cache)

    return lookup


def refusal(
    file_path: Path,
    request: HttpRequest,
    url_kwargs: dict[str, object],
    dep_cache: dict[str, Any],
    *,
    middleware: bool = False,
) -> HttpResponseRedirect | None:
    """Return the login redirect a page owes `request`, raising a 403 or a 404 instead.

    `middleware` adds the login a path past `LoginRequiredMiddleware` misses.
    """
    access = page_access(file_path)
    if access.broken is not None:
        refuse_broken(access.broken)
    rule = access.rule
    if not rule.requirements:
        if (
            middleware
            and middleware_login(rule)
            and not signed_in(getattr(request, "user", None))
        ):
            return login_redirect(request.get_full_path())
        return None
    lookup = url_lookup(request, url_kwargs, dep_cache)
    denial = check(getattr(request, "user", None), rule, lookup)
    return None if denial is None else answer(denial, request.get_full_path())


def admits(file_path: Path, request: HttpRequest) -> bool:
    """Return whether `request` passes the rule of the page at `file_path` unasked.

    A rule naming an object refuses, since only the page render may load it.
    """
    access = page_access(file_path)
    if access.broken is not None:
        return False
    rule = access.rule
    user = getattr(request, "user", None)
    if middleware_login(rule):
        return signed_in(user)
    return check(user, rule) is None


def forget_page_access(**kwargs: object) -> None:
    """Drop every memoised rule, so a settings reload reads the chains again."""
    _PAGES.clear()


__all__ = [
    "AccessStamp",
    "PageAccess",
    "Proxy",
    "TreeDeclarations",
    "access_stamp",
    "admits",
    "anonymous_admitted",
    "forget_page_access",
    "left_to_middleware",
    "middleware_login",
    "owner_access",
    "page_access",
    "refusal",
    "refuse_broken",
    "url_lookup",
]
