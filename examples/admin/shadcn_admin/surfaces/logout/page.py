from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from shadcn_admin import utils

from next.forms import action


@action("admin:logout")
def admin_logout(request: HttpRequest) -> HttpResponse:
    """Log the user out and redirect to the login page."""
    logout(request)
    return HttpResponseRedirect(utils.login_url())
