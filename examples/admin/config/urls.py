from django.contrib import admin
from django.urls import include, path, reverse_lazy
from django.views.generic import RedirectView


admin.autodiscover()

urlpatterns = [
    path("", RedirectView.as_view(url=reverse_lazy("next:page_"), permanent=False)),
    path("admin/", include("next.urls")),
]
