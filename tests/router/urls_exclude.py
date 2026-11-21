from django.http import HttpRequest, HttpResponse
from django.urls import LocalePrefixPattern, URLResolver, include, path, re_path


def view(request: HttpRequest) -> HttpResponse:
    del request
    return HttpResponse()


urlpatterns = [
    path("legacy/", include([path("", view), path("reports/<int:pk>/", view)])),
    path("about/", view),
    re_path(r"^old\-site/(?P<slug>[-\w]+)/$", view),
    re_path(r"^feed\.xml$", view),
    re_path(r"^colou?r/$", view),
    re_path(r"^raw/", view),
    URLResolver(LocalePrefixPattern(), [path("shop/", view)]),
]
