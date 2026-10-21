from django.http import HttpRequest
from shadcn_admin.services import Panel, dashboard_panels

from next import context, page


DASHBOARD_PANELS_ZONE = "dashboard-panels"

metadata = {"breadcrumb": "Dashboard"}

page.login_required(inherit=True)
page.permission_required("shadcn_admin.use_admin", inherit=True)


@context("dashboard_panels", zone=DASHBOARD_PANELS_ZONE)
def panels(request: HttpRequest) -> tuple[Panel, ...]:
    """Return the panels the installed apps fill for the visitor, polled as one zone."""
    return dashboard_panels(request.user)
