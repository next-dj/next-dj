"""The access a `page.py` declares and the rule each page answers to under its tree.

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
    answer,
    check,
    declarations,
    declare_public,
    login_by_default,
    login_redirect,
    require_login,
    require_permission,
    signed_in,
)
from next.permissions.markers import OPEN, chained
from next.utils import template_edits_watched

from .loaders import (
    PAGE_MODULE,
    AncestorStamps,
    build_registered_loaders,
    has_load_errors,
    in_page_tree,
    last_load_error,
    load_python_module_memo,
    module_generation,
    module_version,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping
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


class PageDeclarations:
    """The access a `page.py` declares for its own page, and under `inherit` below it.

    Each binds to the file that calls it, read from the stack like `@context` reads it.
    """

    def login_required(self, *, inherit: bool = False) -> None:
        """Require a logged-in user, the way Django's `login_required` does."""
        require_login(registering_file(), inherit=inherit)

    def permission_required(
        self,
        perm: str | tuple[str, ...],
        *,
        obj: type[DObject[Any]] | None = None,
        inherit: bool = False,
    ) -> None:
        """Require every permission in `perm`, checked on the row `obj` names.

        The row loads through `visible_to(scope)` by the URL kwarg named like the model.
        """
        require_permission(registering_file(), perm, _url_model(obj), inherit=inherit)

    def public(self, *, inherit: bool = False) -> None:
        """Lift what every `page.py` above requires, for this page or the pages below.

        A requirement declared here, or by a `page.py` below, still binds.
        """
        declare_public(registering_file(), inherit=inherit)


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
    """Load every `page.py` of the chain once and join what each declares, root first.

    A `page.py` above passes down what it declares with `inherit=True` alone.
    """
    version = declarations.version
    ancestors, _modules = AncestorStamps.begin(file_path).loaded()
    paths = ancestors.paths
    broken = None
    if has_load_errors():
        broken = next(
            (path for path in paths if last_load_error(path) is not None), None
        )
    above = map(declarations.inherited, paths[:-1])
    rule = chained((*above, declarations.declared(file_path)))
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
    """Return the rule of the page at `file_path`, from the `page.py` chain to its own.

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


def default_login(rule: AccessRule) -> bool:
    """Return whether a rule asking nothing and not public asks the default login."""
    return rule.undeclared and login_by_default()


def anonymous_admitted(access: PageAccess) -> bool:
    """Return whether the rule `access` holds lets an anonymous request in."""
    rule = access.rule
    return not rule.requirements and not default_login(rule)


def subtree_only(file_path: Path) -> bool:
    """Return whether a `page.py` declares access for the pages below and serves none.

    A layout above does not make it a page, since it has no body of its own.
    """
    module = load_python_module_memo(file_path)
    if module is None or declarations.inherited(file_path) is OPEN:
        return False
    if callable(getattr(module, "render", None)):
        return False
    if isinstance(getattr(module, "template", None), str):
        return False
    return not any(loader.can_load(file_path) for loader in build_registered_loaders())


def routed_pages(pages: Iterable[tuple[str, Path]]) -> Iterator[tuple[str, Path]]:
    """Yield the `(trail, page_file)` pairs of a walk that serve a URL of their own."""
    for trail, page_file in pages:
        if not subtree_only(page_file):
            yield trail, page_file


def refuse_broken(broken: Path) -> None:
    """Raise the import failure of `broken` when failing loudly, otherwise a 404."""
    error = last_load_error(broken)
    if error is not None and fail_loudly():
        raise error
    raise Http404


def _full_path(request: HttpRequest) -> str:
    """Return the path a login sends `request` back to, its own with the query."""
    return request.get_full_path()


def url_lookup(
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
) -> Lookup:
    """Return the lookup of the rows an `obj=` of a page or a `page.py` above names.

    A row read here is the one a `DObject` of the same model reads, from `dep_cache`.
    """

    def lookup(target: AccessObject) -> object:
        if isinstance(target, str):
            msg = (
                f"A page checks a permission on the context {target!r}, which only "
                "a component names. Pass obj=DObject[Model] to "
                "page.permission_required()."
            )
            raise TypeError(msg)
        return url_row(target, request, url_kwargs, dep_cache)

    return lookup


@dataclass(frozen=True, slots=True)
class Gate:
    """The row lookup builder and the login return path a caller of `enforce` brings.

    An open rule builds no lookup, since only a rule with requirements reads a row.
    """

    lookup: Callable[[HttpRequest, Mapping[str, object], dict[str, Any]], Lookup]
    next_url: Callable[[HttpRequest], str]


_PAGE_GATE = Gate(url_lookup, _full_path)


def enforce(
    rule: AccessRule,
    broken: Path | None,
    request: HttpRequest,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
    *,
    undeclared: bool,
    gate: Gate,
) -> HttpResponseRedirect | None:
    """Return the login redirect `rule` owes `request`, raising a 403 or a 404 instead.

    An anonymous request meets the default login before a broken chain is refused.
    """
    user = getattr(request, "user", None)
    if broken is not None:
        if not rule.public and login_by_default() and not signed_in(user):
            return login_redirect(request, gate.next_url(request))
        refuse_broken(broken)
    if not rule.requirements:
        if undeclared and login_by_default() and not signed_in(user):
            return login_redirect(request, gate.next_url(request))
        return None
    denial = check(user, rule, gate.lookup(request, url_kwargs, dep_cache))
    return None if denial is None else answer(denial, request, gate.next_url(request))


def refusal(
    file_path: Path,
    request: HttpRequest,
    url_kwargs: dict[str, object],
    dep_cache: dict[str, Any],
) -> HttpResponseRedirect | None:
    """Return the login redirect a page owes `request`, raising a 403 or a 404 instead.

    A rule asking nothing asks the default login, which a listed middleware meets first.
    """
    access = page_access(file_path)
    rule = access.rule
    return enforce(
        rule,
        access.broken,
        request,
        url_kwargs,
        dep_cache,
        undeclared=rule.undeclared,
        gate=_PAGE_GATE,
    )


def forget_page_access(**kwargs: object) -> None:
    """Drop every memoised rule, so a settings reload reads the chains again."""
    _PAGES.clear()


__all__ = [
    "AccessStamp",
    "Gate",
    "PageAccess",
    "PageDeclarations",
    "access_stamp",
    "anonymous_admitted",
    "default_login",
    "enforce",
    "forget_page_access",
    "owner_access",
    "page_access",
    "refusal",
    "refuse_broken",
    "routed_pages",
    "subtree_only",
    "url_lookup",
]
