from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("account/logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path("", include("next.urls")),
]
