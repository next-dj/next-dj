from typing import Any

from django import forms as django_forms
from django.dispatch import receiver
from django.utils import timezone
from shadcn_admin.services import NavLink, Panel, PanelRow, Visitor
from shadcn_admin.signals import nav_requested, panels_requested

from admin_audit.models import AdminActivityLog
from next.forms.signals import action_dispatched
from next.urls import page_reverse


READ_LOG = "admin_audit.view_adminactivitylog"
FEED_SIZE = 5


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
    dep_cache: dict[str, Any] | None = None,
    **kwargs,
) -> None:
    """Append one `AdminActivityLog` row for every dispatched admin action."""
    kind = _ACTION_TO_KIND.get(action_name)
    if kind is None:
        return
    kwargs = url_kwargs or {}
    spec = (dep_cache or {}).get("admin_spec")
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


@receiver(nav_requested)
def offer_activity_link(user: Visitor, **kwargs) -> tuple[NavLink, ...]:
    """Add the activity feed to the sidebar of a user who may read the log."""
    if not user.has_perm(READ_LOG):
        return ()
    return (NavLink("Auditing", "Activity feed", page_reverse("activity")),)


@receiver(panels_requested)
def offer_recent_activity(user: Visitor, **kwargs) -> tuple[Panel, ...]:
    """Fill the dashboard with the newest admin actions for a user who may read them."""
    if not user.has_perm(READ_LOG):
        return ()
    rows = tuple(
        PanelRow(
            entry.action,
            entry.object_repr,
            f"{entry.user or '—'} · "
            f"{timezone.localtime(entry.timestamp):%Y-%m-%d %H:%M:%S}",
        )
        for entry in AdminActivityLog.objects.visible_to(user).recent(FEED_SIZE)
    )
    return (
        Panel(
            "Recent activity",
            rows,
            "No activity yet.",
            link_label="All activity",
            link_url=page_reverse("activity"),
        ),
    )
