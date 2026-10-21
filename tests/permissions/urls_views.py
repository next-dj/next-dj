from django.contrib.auth.decorators import (
    login_not_required,
    login_required,
    permission_required,
)
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.urls import include, path, re_path
from django.utils.decorators import method_decorator
from django.views import View


def worker(request: HttpRequest) -> HttpResponse:
    return HttpResponse("worker")


@login_not_required
def robots(request: HttpRequest) -> HttpResponse:
    return HttpResponse("robots")


@method_decorator(login_not_required, name="dispatch")
class Report(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse("report")


@login_required
def account(request: HttpRequest) -> HttpResponse:
    return HttpResponse("account")


@permission_required("auth.view_user")
def staff(request: HttpRequest) -> HttpResponse:
    return HttpResponse("staff")


class Members(LoginRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse("members")


@login_not_required
@permission_required("auth.view_user", raise_exception=True)
def secret(request: HttpRequest) -> HttpResponse:
    return HttpResponse("secret")


class Gate(View):
    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_staff:
            return HttpResponseForbidden()
        return HttpResponse("gate")


urlpatterns = [
    path("sw.js", worker),
    re_path(r"^api/", include([re_path(r"^report/$", Report.as_view())])),
    path("robots.txt", robots),
    path("account/", account),
    path("staff/", staff),
    path("members/", Members.as_view()),
    path("gate/", Gate.as_view()),
    path("secret/", secret),
    path("", include("next.urls")),
]
