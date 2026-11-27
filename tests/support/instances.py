from django.http import HttpRequest, HttpResponse
from django.urls import include, path


def index(request: HttpRequest) -> HttpResponse:
    return HttpResponse()


_POLLS = ([path("", index, name="index")], "polls")

urlpatterns = [
    path("first/", include(_POLLS, namespace="first")),
    path("second/", include(_POLLS, namespace="second")),
]
