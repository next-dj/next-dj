"""Guard and permission-hook enforcement for the form action dispatch pipeline."""

from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect, QueryDict
from django.shortcuts import resolve_url

from next.forms.signals import form_access_denied
from next.forms.uid import ORIGIN_FIELD_NAME, validated_origin_path

from .build import _resolve_and_call


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from django import forms as django_forms
    from django.http import HttpRequest

    from next.forms.backends import ActionGuard
    from next.forms.base import PermissionOutcome

    from . import _DispatchState

    class _ViewPermissionHook(Protocol):
        """Anything exposing a DI-resolved view-level check_permissions classmethod."""

        @classmethod
        def check_permissions(cls) -> "PermissionOutcome": ...

    class _ObjectPermissionHook(Protocol):
        """A bound form exposing a DI-resolved object-level permission method."""

        def has_object_permission(self) -> "PermissionOutcome": ...


def _redirect_to_login(next_url: str) -> HttpResponseRedirect:
    """Build the LOGIN_URL redirect carrying `next_url`.

    Mirrors `django.contrib.auth.views.redirect_to_login` without importing
    contrib.auth.views, whose module-level `get_user_model()` call requires
    django.contrib.auth in INSTALLED_APPS.
    """
    scheme, netloc, path, query, fragment = urlsplit(resolve_url(settings.LOGIN_URL))
    querystring = QueryDict(query, mutable=True)
    querystring[REDIRECT_FIELD_NAME] = next_url
    return HttpResponseRedirect(
        urlunsplit((scheme, netloc, path, querystring.urlencode(safe="/"), fragment))
    )


def _check_access(
    request: "HttpRequest", guard: "ActionGuard"
) -> HttpResponseRedirect | None:
    """Enforce the action guard with `AccessMixin` semantics.

    Anonymous users get a login redirect whose `next` is the validated posted
    origin, authenticated users missing a permission raise PermissionDenied.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        origin = validated_origin_path(request.POST.get(ORIGIN_FIELD_NAME))
        return _redirect_to_login(origin or "/")
    if guard.permissions and not user.has_perms(guard.permissions):
        raise PermissionDenied
    return None


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
    layer: Literal["view", "object"],
    reason: Literal["raised", "denied", "response"],
    sender: type,
) -> None:
    """Send `form_access_denied` when any receiver is connected."""
    if form_access_denied.receivers:
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
