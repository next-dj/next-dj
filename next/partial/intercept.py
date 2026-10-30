"""Selection of the intercept page that serves a zone request over its target."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final, cast

from django.core.exceptions import PermissionDenied

from next.deps import render_dep_cache
from next.intercept import intercepts
from next.pages.access import page_access, refusal
from next.pages.loaders import last_load_error

from .errors import ForeignPageNotAuthorizedError
from .registry import zones_served


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest, HttpResponseRedirect

    from next.intercept import Intercept


_GRANTS_ATTR: Final = "_next_partial_grants"


@dataclass(frozen=True, slots=True)
class _Grant:
    """One admission of a page to a user under the URL kwargs it was checked with."""

    page_path: "Path"
    url_kwargs: dict[str, Any]
    user: object

    def same(self, other: "_Grant") -> bool:
        """Whether `other` admitted the same page, kwargs and user object."""
        return (
            self.user is other.user
            and self.page_path == other.page_path
            and self.url_kwargs == other.url_kwargs
        )


def pick_intercept(target_file: "Path", zones: tuple[str, ...]) -> "Intercept | None":
    """Return the rule whose page serves `zones` over `target_file`, never rendering.

    The first rule serving them all wins, and one that fails to compose is skipped.
    """
    rules = intercepts.of(target_file)
    if not rules or not zones:
        return None
    return next(
        (rule for rule in rules if _serves_all(rule.file, target_file, zones)), None
    )


def admit(
    page_path: "Path",
    request: "HttpRequest",
    url_kwargs: "Mapping[str, Any]",
    dep_cache: dict[str, Any] | None = None,
) -> "HttpResponseRedirect | None":
    """Return the login redirect the rule of `page_path` owes, raising 403 or 404.

    A grant is kept on the request for the same page, kwargs and user, a refusal never.
    """
    grant = _Grant(page_path, dict(url_kwargs), getattr(request, "user", None))
    granted = _grants(request)
    if any(grant.same(held) for held in granted):
        return None
    cache = render_dep_cache(request) if dep_cache is None else dep_cache
    redirect = refusal(page_path, request, grant.url_kwargs, cache)
    if redirect is None:
        granted.append(grant)
    return redirect


def refuse_intercept(
    page_path: "Path", request: "HttpRequest", url_kwargs: "Mapping[str, Any]"
) -> None:
    """Raise `ForeignPageNotAuthorizedError` when the rule of a page refuses `request`.

    A broken chain raises its import failure, as the view of a routed page does.
    """
    try:
        redirect = admit(page_path, request, url_kwargs)
    except PermissionDenied:
        raise ForeignPageNotAuthorizedError(page_path, HTTPStatus.FORBIDDEN) from None
    if redirect is not None:
        raise ForeignPageNotAuthorizedError(page_path, redirect.status_code)


def resolve_served_page(
    target_file: "Path",
    zones: tuple[str, ...],
    request: "HttpRequest",
    url_kwargs: "Mapping[str, Any]",
) -> "Path":
    """Return the page that serves `zones` of `target_file`, its access checked first.

    A refused intercept raises `ForeignPageNotAuthorizedError`, never falling back.
    """
    rule = pick_intercept(target_file, zones)
    if rule is None:
        return target_file
    refuse_intercept(rule.file, request, url_kwargs)
    return rule.file


def refuse_access(
    page_path: "Path", request: "HttpRequest", url_kwargs: "Mapping[str, Any]"
) -> None:
    """Raise when the rule of a page rendered under another URL refuses `request`.

    A broken `page.py` of its chain raises its import failure, not a 404 of the caller.
    """
    broken = page_access(page_path).broken
    error = None if broken is None else last_load_error(broken)
    if error is not None:
        raise error
    refuse_intercept(page_path, request, url_kwargs)


def _grants(request: "HttpRequest") -> list[_Grant]:
    """Return the grants kept on `request`, an empty list kept on its first read."""
    held = getattr(request, _GRANTS_ATTR, None)
    if held is not None:
        return cast("list[_Grant]", held)
    fresh: list[_Grant] = []
    setattr(request, _GRANTS_ATTR, fresh)
    return fresh


def _serves_all(page_path: "Path", target_file: "Path", zones: tuple[str, ...]) -> bool:
    """Return True when the intercept page serves every one of `zones` over a target."""
    served = zones_served(page_path, target_file)
    return all(name in served for name in zones)


__all__ = [
    "admit",
    "pick_intercept",
    "refuse_access",
    "refuse_intercept",
    "resolve_served_page",
]
