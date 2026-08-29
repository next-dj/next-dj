from django.contrib import admin
from django.urls import include, path, reverse_lazy
from django.views.generic import RedirectView


admin.autodiscover()

urlpatterns = [
    # `next:page_` is the router's name for `shadcn_admin/surfaces/page.py`.
    path("", RedirectView.as_view(url=reverse_lazy("next:page_"), permanent=False)),
    path("admin/", include("next.urls")),
]
