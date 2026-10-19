from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
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


urlpatterns = [
    path("sw.js", worker),
    re_path(r"^api/", include([re_path(r"^report/$", Report.as_view())])),
    path("robots.txt", robots),
    path("", include("next.urls")),
]
