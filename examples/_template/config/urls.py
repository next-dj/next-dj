from django.contrib import admin
from django.urls import include, path

from next.urls import pages


urlpatterns = [
    path("admin/", admin.site.urls),
    path("myapp/", pages("myapp")),
    path("", include("next.urls")),
]
