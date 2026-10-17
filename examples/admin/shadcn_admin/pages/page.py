from django.http import HttpRequest
from shadcn_admin.services import Panel, dashboard_panels

from next import context


DASHBOARD_PANELS_ZONE = "dashboard-panels"

metadata = {"breadcrumb": "Dashboard"}


@context("dashboard_panels", zone=DASHBOARD_PANELS_ZONE)
def panels(request: HttpRequest) -> tuple[Panel, ...]:
    """Return the panels the installed apps fill for the visitor, polled as one zone."""
    return dashboard_panels(request.user)
