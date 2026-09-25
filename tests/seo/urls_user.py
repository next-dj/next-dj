from django.http import HttpResponse
from django.urls import include, path

from next.seo import views as seo_views


def mine(request):
    return HttpResponse("mine")


urlpatterns = [
    path("", include("next.urls")),
    path("sitemap.xml", mine),
    path("robots.txt", mine),
    path("direct/robots.txt", seo_views.robots_view),
]
