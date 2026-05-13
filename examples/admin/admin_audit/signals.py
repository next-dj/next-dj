from typing import Any

from django import forms as django_forms
from django.dispatch import receiver

from admin_audit.models import AdminActivityLog
from next.forms.signals import action_dispatched


_ACTION_TO_KIND = {
    "admin:add": AdminActivityLog.ACTION_ADD,
    "admin:change": AdminActivityLog.ACTION_CHANGE,
    "admin:delete": AdminActivityLog.ACTION_DELETE,
    "admin:bulk_action": AdminActivityLog.ACTION_BULK,
}


@receiver(action_dispatched)
def log_admin_action(
    action_name: str = "",
    form: django_forms.BaseForm | None = None,
    url_kwargs: dict[str, Any] | None = None,
    response_status: int = 0,
    **_: object,
) -> None:
    """Append one `AdminActivityLog` row for every dispatched admin action."""
    kind = _ACTION_TO_KIND.get(action_name)
    if kind is None:
        return
    kwargs = url_kwargs or {}
    spec = getattr(form, "_admin_spec", None) if form is not None else None
    user = None
    if spec is not None and spec.request.user.is_authenticated:
        user = spec.request.user
    object_repr = ""
    if form is not None and getattr(form, "instance", None) is not None:
        object_repr = str(form.instance)[:200]
    AdminActivityLog.objects.create(
        user=user,
        action=kind,
        app_label=str(kwargs.get("app_label", "")),
        model_name=str(kwargs.get("model_name", "")),
        object_repr=object_repr,
        response_status=response_status,
    )
