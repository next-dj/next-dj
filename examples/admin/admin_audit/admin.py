from django.utils import timezone
from shadcn_admin.services import (
    NavLink,
    Panel,
    PanelRow,
    Visitor,
    register_nav,
    register_panel,
)

from admin_audit.models import AdminActivityLog
from next.urls import page_reverse


READ_LOG = "admin_audit.view_adminactivitylog"
FEED_SIZE = 5


@register_nav
def activity_link(user: Visitor) -> tuple[NavLink, ...]:
    """Add the activity feed to the sidebar of a user who may read the log."""
    if not user.has_perm(READ_LOG):
        return ()
    return (NavLink("Auditing", "Activity feed", page_reverse("activity")),)


@register_panel
def recent_activity(user: Visitor) -> tuple[Panel, ...]:
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
