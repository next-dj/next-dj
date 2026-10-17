"""Guard and permission-hook enforcement for the form action dispatch pipeline."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from django.core.exceptions import PermissionDenied
from django.forms import BaseModelForm
from django.http import HttpResponse, HttpResponseRedirect

from next.caches import PageCache
from next.forms.signals import form_access_denied
from next.forms.uid import posted_origin_path
from next.pages import page
from next.pages.access import (
    AccessStamp,
    access_stamp,
    left_to_middleware,
    owner_access,
    page_access,
    refuse_broken,
    url_lookup,
)
from next.permissions.access import (
    answer,
    check,
    login_middleware_active,
    login_redirect,
    signed_in,
)
from next.permissions.manager import permission_manager
from next.permissions.markers import OPEN, AccessRule, joined

from .build import _resolve_and_call


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from django import forms as django_forms
    from django.db.models import Model
    from django.http import HttpRequest, HttpResponseBase

    from next.forms.backends import ActionGuard
    from next.forms.base import PermissionOutcome
    from next.forms.origin import OriginMatch

    from . import _DispatchState

    class _ViewPermissionHook(Protocol):
        """Anything exposing a DI-resolved view-level check_permissions classmethod."""

        @classmethod
        def check_permissions(cls) -> "PermissionOutcome": ...

    class _ObjectPermissionHook(Protocol):
        """A bound form exposing a DI-resolved object-level permission method."""

        def has_object_permission(self) -> "PermissionOutcome": ...

    class _PermissionHolder(Protocol):
        """A user Django's auth backends answer `has_perm` for."""

        def has_perm(self, perm: str, obj: "Model | None" = None) -> bool: ...

        def has_perms(self, perms: tuple[str, ...]) -> bool: ...


_NO_PERMISSIONS: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Split:
    """The guard permissions checked up front and the ones a predicate answers."""

    version: int
    static: tuple[str, ...]
    registered: tuple[str, ...]


_splits: dict[tuple[str, ...], _Split] = {}


def _split_permissions(permissions: tuple[str, ...]) -> _Split:
    """Split guard permissions into the static ones and the registered object ones.

    The split is kept per tuple until the permission registry changes.
    """
    version = permission_manager.version
    held = _splits.get(permissions)
    if held is not None and held.version == version:
        return held
    registered = tuple(
        perm for perm in permissions if permission_manager.is_registered(perm)
    )
    static = (
        tuple(perm for perm in permissions if perm not in registered)
        if registered
        else permissions
    )
    held = _splits[permissions] = _Split(version, static, registered)
    return held


def _object_permissions(guard: "ActionGuard | None") -> tuple[str, ...]:
    """Return the guard permissions a registered predicate answers per object."""
    if guard is None or not guard.permissions:
        return _NO_PERMISSIONS
    return _split_permissions(guard.permissions).registered


def _check_access(
    request: "HttpRequest", guard: "ActionGuard", *, object_stage: bool = False
) -> HttpResponseRedirect | None:
    """Enforce the action guard with `AccessMixin` semantics.

    Anonymous users are sent to log in, and `object_stage` defers registered perms.
    """
    if not signed_in(getattr(request, "user", None)):
        return login_redirect(posted_origin_path(request) or "/")
    permissions = guard.permissions
    if permissions and object_stage:
        permissions = _split_permissions(permissions).static
    user = cast("_PermissionHolder", request.user)
    if permissions and not user.has_perms(permissions):
        raise PermissionDenied
    return None


@dataclass(frozen=True, slots=True)
class _Tree:
    """The rules over one action and its origin page, joined once per change.

    An open rule defers to `LoginRequiredMiddleware`, and a posted origin opens nothing.
    """

    stamp: AccessStamp | None
    rule: AccessRule
    broken: Path | None
    deferred: bool


_trees: PageCache[tuple[str | None, Path | None], _Tree] = PageCache()


def _build_tree(
    file_path: str | None, origin_path: Path | None, stamp: AccessStamp | None
) -> _Tree:
    """Read the rules of the action owner and of the origin page and join them.

    A file of a tree answers to the page of its directory.
    """
    owner = None if file_path is None else owner_access(Path(file_path))
    origin = None if origin_path is None else page_access(origin_path)
    accesses = [held for held in (owner, origin) if held is not None]
    broken = next((held.broken for held in accesses if held.broken is not None), None)
    owned = OPEN if owner is None else owner.rule
    deferred = left_to_middleware(owned) or (
        origin is not None and left_to_middleware(origin.rule)
    )
    rule = joined(held.rule for held in accesses)
    return _Tree(None if broken is not None else stamp, rule, broken, deferred)


def _tree(file_path: str | None, origin_path: Path | None) -> _Tree:
    """Return the joined rules over an action, one memo read while no rule changed.

    Past any load, or under watched edits, the rules are read and joined again.
    """
    key = (file_path, origin_path)
    held = _trees.get(key)
    stamp = access_stamp()
    if held is None or held.stamp is None or held.stamp != stamp:
        held = _trees[key] = _build_tree(file_path, origin_path, stamp)
    return held


def _check_tree_access(
    request: "HttpRequest",
    file_path: object,
    origin_match: "OriginMatch | None",
    dep_cache: dict[str, Any],
    *,
    action_name: str,
    uid: str | None,
    sender: type,
) -> HttpResponseRedirect | None:
    """Enforce the rule of the action owner and of the origin page before its guard.

    A forged origin adds its rule to the owner's and lifts none of it.
    """
    origin_path = origin_match.page_path if origin_match is not None else None
    tree = _tree(file_path if isinstance(file_path, str) else None, origin_path)
    if tree.broken is not None:
        refuse_broken(tree.broken)
    rule = tree.rule
    if not rule.requirements:
        if (
            tree.deferred
            and login_middleware_active()
            and not signed_in(getattr(request, "user", None))
        ):
            return login_redirect(posted_origin_path(request) or "/")
        return None
    url_kwargs = dict(origin_match.url_kwargs) if origin_match is not None else {}
    lookup = url_lookup(request, url_kwargs, dep_cache)
    denial = check(getattr(request, "user", None), rule, lookup)
    if denial is None:
        return None
    try:
        return answer(denial, posted_origin_path(request) or "/")
    except PermissionDenied:
        _emit_form_access_denied(
            request, action_name, uid, layer="page", reason="denied", sender=sender
        )
        raise


def _check_page_access(
    request: "HttpRequest",
    origin_match: "OriginMatch | None",
    *,
    action_name: str,
    uid: str | None,
    sender: type,
) -> "HttpResponseBase | None":
    """Return the origin page's own short-circuit for this action POST, or None.

    The action endpoint is not the page URL, so the page view never runs and this is
    the only place a guarded page refuses a submission it would refuse a visit.
    """
    if origin_match is None or origin_match.page_path is None:
        return None
    denial, _dynamic = page.authorization_outcome(
        origin_match.page_path, request, origin_match.origin, origin_match.url_kwargs
    )
    if denial is not None:
        _emit_form_access_denied(
            request, action_name, uid, layer="page", reason="response", sender=sender
        )
    return denial


def _normalize_permission(raw: object) -> "HttpResponse | None":
    """Map a permission-hook return to a denial response, or None to allow."""
    if raw is None or raw is True:
        return None
    if raw is False:
        raise PermissionDenied
    if isinstance(raw, HttpResponse):
        return raw
    msg = (
        f"permission hook returned unsupported {type(raw).__name__}, return "
        "None or True to allow, False or raise PermissionDenied to deny, or an "
        "HttpResponse to short-circuit."
    )
    raise TypeError(msg)


def _emit_form_access_denied(
    request: "HttpRequest",
    action_name: str,
    uid: str | None,
    *,
    layer: Literal["page", "view", "object"],
    reason: Literal["raised", "denied", "response"],
    sender: type,
) -> None:
    """Send `form_access_denied` when a receiver listens for this sender."""
    if form_access_denied.has_listeners(sender):
        form_access_denied.send(
            sender=sender,
            action_name=action_name,
            uid=uid,
            request=request,
            layer=layer,
            reason=reason,
        )


def _enforce_permission_hook(
    request: "HttpRequest",
    action_name: str,
    state: "_DispatchState",
    *,
    hook: "Callable[..., Any] | None",
    layer: Literal["view", "object"],
) -> "HttpResponse | None":
    """Run a DI-resolved permission hook for one layer, emitting on a denial.

    A `None` hook means the layer is undeclared, so dispatch continues.
    """
    if hook is None:
        return None
    try:
        raw = _resolve_and_call(
            hook, request, state.url_kwargs, deps=(state.dep_cache, state.dep_stack)
        )
    except PermissionDenied:
        _emit_form_access_denied(
            request,
            action_name,
            state.uid,
            layer=layer,
            reason="raised",
            sender=state.signal_sender,
        )
        raise
    return _resolve_permission_outcome(
        raw, request, action_name, state.uid, layer=layer, sender=state.signal_sender
    )


def _resolve_permission_outcome(
    raw: object,
    request: "HttpRequest",
    action_name: str,
    uid: str | None,
    *,
    layer: Literal["view", "object"],
    sender: type,
) -> "HttpResponse | None":
    """Normalise a hook return, emitting the audit signal on a denial."""
    try:
        denial = _normalize_permission(raw)
    except PermissionDenied:
        _emit_form_access_denied(
            request, action_name, uid, layer=layer, reason="denied", sender=sender
        )
        raise
    if denial is not None:
        _emit_form_access_denied(
            request, action_name, uid, layer=layer, reason="response", sender=sender
        )
    return denial


def _enforce_view_permissions(
    form_class: type, request: "HttpRequest", action_name: str, state: "_DispatchState"
) -> "HttpResponse | None":
    """Run the view-level check_permissions hook, emitting on a denial."""
    present = getattr(form_class, "_has_check_permissions", False)
    hook = (
        cast("type[_ViewPermissionHook]", form_class).check_permissions
        if present
        else None
    )
    return _enforce_permission_hook(
        request, action_name, state, hook=hook, layer="view"
    )


def _permission_object(form: "django_forms.Form") -> "Model | None":
    """Return the saved instance a model form edits, `None` for anything else."""
    if not isinstance(form, BaseModelForm):
        return None
    instance: Model = form.instance
    return None if instance._state.adding else instance


def _enforce_registered_permissions(
    form: "django_forms.Form",
    request: "HttpRequest",
    action_name: str,
    permissions: tuple[str, ...],
    state: "_DispatchState",
) -> None:
    """Check each registered guard permission against the object the form binds.

    A denial raises PermissionDenied, as a `has_object_permission` returning False does.
    """
    if not permissions:
        return
    obj = _permission_object(form)
    user = cast("_PermissionHolder", request.user)
    for perm in permissions:
        if not user.has_perm(perm, obj):
            _emit_form_access_denied(
                request,
                action_name,
                state.uid,
                layer="object",
                reason="denied",
                sender=state.signal_sender,
            )
            raise PermissionDenied


def _enforce_object_permissions(
    form: "django_forms.Form",
    request: "HttpRequest",
    action_name: str,
    state: "_DispatchState",
) -> "HttpResponse | None":
    """Run the object-level has_object_permission hook after the form binds."""
    present = getattr(type(form), "_has_object_permission", False)
    hook = (
        cast("_ObjectPermissionHook", form).has_object_permission if present else None
    )
    return _enforce_permission_hook(
        request, action_name, state, hook=hook, layer="object"
    )
