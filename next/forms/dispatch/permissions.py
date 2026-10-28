"""Guard and permission-hook enforcement for the form action dispatch pipeline."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from django.core.exceptions import PermissionDenied
from django.forms import BaseModelForm
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect

from next.caches import PageCache
from next.forms.signals import form_access_denied
from next.forms.uid import posted_origin_path
from next.pages import page
from next.pages.access import (
    AccessStamp,
    Gate,
    access_stamp,
    enforce,
    owner_access,
    page_access,
    url_lookup,
)
from next.pages.loaders import PAGE_MODULE, in_page_tree, load_page_module
from next.permissions.access import declared, login_redirect, signed_in
from next.permissions.manager import permission_manager
from next.permissions.markers import OPEN, AccessRule, Requirement, joined

from .build import _resolve_and_call


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol

    from django import forms as django_forms
    from django.db.models import Model
    from django.http import HttpRequest, HttpResponseBase

    from next.forms.backends import ActionGuard, ActionMeta
    from next.forms.base import PermissionOutcome
    from next.forms.origin import OriginMatch
    from next.pages.access import PageAccess
    from next.permissions.access import Lookup
    from next.permissions.markers import AccessObject

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

FORM_OBJECT: Final = "form"
"""The object of a guard permission that dispatch asks of the bound form."""

COMPONENT_MODULE: Final = "component.py"

type _Branch = Literal["wizard", "handler", "form"]


def _branch(meta: "ActionMeta") -> "_Branch | None":
    """Return the branch dispatch takes for an action, `None` when it declares none.

    The guard reads it too, so only the form branch defers its registered permissions.
    """
    if meta.get("wizard_class") is not None:
        return "wizard"
    if meta.get("form_class") is not None:
        return "form"
    if meta.get("handler") is not None:
        return "handler"
    return None


@dataclass(frozen=True, slots=True)
class _GuardRule:
    """The rule of one guard and the permission registry version it was built under."""

    version: int
    rule: AccessRule


_guard_rules: dict[tuple["ActionGuard", str, bool], _GuardRule] = {}


def _build_guard_rule(
    guard: "ActionGuard", source: Path, *, on_form: bool
) -> AccessRule:
    """Return the login and the permissions a guard asks, bound to its file.

    On the form branch a permission `add_perm` registered is asked of the bound form.
    """
    registered = tuple(
        perm
        for perm in guard.permissions
        if on_form and permission_manager.is_registered(perm)
    )
    static = (
        tuple(perm for perm in guard.permissions if perm not in registered)
        if registered
        else guard.permissions
    )
    requirements = [Requirement("login", source)]
    if static:
        requirements.append(Requirement("permission", source, static))
    if registered:
        requirements.append(Requirement("permission", source, registered, FORM_OBJECT))
    return AccessRule(tuple(requirements), sources=(source,))


def guard_rule(meta: "ActionMeta") -> AccessRule:
    """Return the rule the `Meta` guard of an action asks, the one dispatch enforces.

    The rule is kept per guard until the permission registry changes.
    """
    guard = meta.get("guard")
    if guard is None:
        return OPEN
    file_path = meta["file_path"]
    on_form = _branch(meta) == "form"
    key = (guard, file_path, on_form)
    version = permission_manager.version
    held = _guard_rules.get(key)
    if held is None or held.version != version:
        rule = _build_guard_rule(guard, Path(file_path), on_form=on_form)
        held = _guard_rules[key] = _GuardRule(version, rule)
    return held.rule


def _object_permissions(rule: AccessRule) -> tuple[str, ...]:
    """Return the guard permissions dispatch asks of the bound form, in guard order."""
    for requirement in rule.requirements:
        if requirement.obj == FORM_OBJECT:
            return requirement.perms
    return _NO_PERMISSIONS


def _origin_path(request: "HttpRequest") -> str:
    """Return the page a login sends an action POST back to, the root without one."""
    return posted_origin_path(request) or "/"


def _check_access(
    request: "HttpRequest", rule: AccessRule
) -> HttpResponseRedirect | None:
    """Enforce a guard rule up to the bound form, with `AccessMixin` semantics.

    Anonymous users are sent to log in, and a permission on the form waits for it.
    """
    user = getattr(request, "user", None)
    for requirement in rule.requirements:
        if requirement.kind == "login":
            if not signed_in(user):
                return login_redirect(request, _origin_path(request))
        elif requirement.obj is None and not cast("_PermissionHolder", user).has_perms(
            requirement.perms
        ):
            raise PermissionDenied
    return None


@dataclass(frozen=True, slots=True)
class OwnerRule:
    """The rule an action owes its file, with the broken file of its chain."""

    rule: AccessRule
    broken: Path | None


def owner_rule(file_path: Path) -> OwnerRule | None:
    """Return the rule an action answers to by its file, `None` when nothing owns it.

    A `component.py` adds what it declares, so its actions refuse whom it hides from.
    """
    owner = owner_access(file_path)
    own = declared(file_path) if file_path.name == COMPONENT_MODULE else OPEN
    if not own.requirements:
        return None if owner is None else OwnerRule(owner.rule, owner.broken)
    if owner is None:
        return OwnerRule(own, None)
    return OwnerRule(joined((owner.rule, own)), owner.broken)


def _action_lookup(
    request: "HttpRequest",
    url_kwargs: "Mapping[str, object]",
    dep_cache: dict[str, Any],
) -> "Lookup":
    """Return the row lookup of an action, which finds no component context.

    A POST renders no component, so a permission on its context finds no object.
    """
    rows = url_lookup(request, url_kwargs, dep_cache)

    def lookup(target: "AccessObject") -> object:
        return None if isinstance(target, str) else rows(target)

    return lookup


_ACTION_GATE: Final = Gate(_action_lookup, _origin_path)

_NO_KWARGS: Final[MappingProxyType[str, object]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class _Tree:
    """The rules over one action and its origin page, joined once per change.

    An open rule asks the default login, and a posted origin opens nothing.
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

    A tree file answers to the page of its directory, a `component.py` to itself too.
    """
    owner = None if file_path is None else owner_rule(Path(file_path))
    origin = None if origin_path is None else page_access(origin_path)
    accesses: list[OwnerRule | PageAccess] = [
        held for held in (owner, origin) if held is not None
    ]
    broken = next((held.broken for held in accesses if held.broken is not None), None)
    owned = OPEN if owner is None else owner.rule
    deferred = owned.undeclared or (origin is not None and origin.rule.undeclared)
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
    url_kwargs = origin_match.url_kwargs if origin_match is not None else _NO_KWARGS
    try:
        return enforce(
            tree.rule,
            tree.broken,
            request,
            url_kwargs,
            dep_cache,
            undeclared=tree.deferred,
            gate=_ACTION_GATE,
        )
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
    """Return the origin page's own refusal of this action POST, or None.

    The page view never runs for an action URL, so a guarded page refuses here.
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


def owner_page(file_path: object) -> Path | None:
    """Return the `page.py` whose `render()` an action answers to, `None` without one.

    A file of a page tree answers to the page of its directory, as for its rules.
    """
    if not isinstance(file_path, str):
        return None
    directory = Path(file_path).parent
    if not in_page_tree(directory):
        return None
    owner = directory / PAGE_MODULE
    module, _error = load_page_module(owner)
    return owner if callable(getattr(module, "render", None)) else None


_owner_pages: PageCache[str, tuple[AccessStamp, Path | None]] = PageCache()


def _held_owner_page(file_path: object) -> Path | None:
    """Return `owner_page`, one memo read while no page module has loaded again."""
    stamp = access_stamp()
    if stamp is None or not isinstance(file_path, str):
        return owner_page(file_path)
    held = _owner_pages.get(file_path)
    if held is None or held[0] != stamp:
        held = _owner_pages[file_path] = (stamp, owner_page(file_path))
    return held[1]


def _check_owner_page_access(
    request: "HttpRequest",
    file_path: object,
    origin_match: "OriginMatch",
    *,
    action_name: str,
    uid: str | None,
    sender: type,
) -> "HttpResponseBase | None":
    """Return the owner page's own refusal of this action POST, or None.

    Its `render()` guards the action, so an origin outside its subtree is refused.
    """
    owner = _held_owner_page(file_path)
    if owner is None:
        return None
    origin_page = origin_match.page_path
    if origin_page is None:
        _emit_form_access_denied(
            request, action_name, uid, layer="page", reason="denied", sender=sender
        )
        return HttpResponseBadRequest("Missing or invalid _next_form_origin")
    if not origin_page.parent.is_relative_to(owner.parent):
        _emit_form_access_denied(
            request, action_name, uid, layer="page", reason="denied", sender=sender
        )
        raise PermissionDenied
    if origin_page == owner:
        # The origin check already ran this page's `render()`.
        return None
    denial, _dynamic = page.authorization_outcome(
        owner, request, origin_match.origin, origin_match.url_kwargs
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
