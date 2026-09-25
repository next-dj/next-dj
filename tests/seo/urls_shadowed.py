from django.http import HttpResponse
from django.urls import include, path


def mine(request):
    return HttpResponse("mine")


urlpatterns = [
    path("sitemap.xml", mine),
    path("robots.txt", mine),
    path("", include("next.urls")),
]
