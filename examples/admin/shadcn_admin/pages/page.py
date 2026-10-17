from admin_audit.models import AdminActivityLog, AdminActivityLogQuerySet

from next import context


RECENT_ACTIVITY_ZONE = "recent-activity"

metadata = {"breadcrumb": "Dashboard"}


@context("recent_activity", zone=RECENT_ACTIVITY_ZONE)
def recent_activity() -> AdminActivityLogQuerySet:
    """Return the five newest admin actions, read only where the template iterates."""
    return AdminActivityLog.objects.recent(5)
