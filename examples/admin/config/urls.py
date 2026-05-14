from django.contrib import admin
from django.urls import include, path, reverse_lazy
from django.views.generic import RedirectView


admin.autodiscover()

urlpatterns = [
    # Send bare `/` to the dashboard so a fresh visit doesn't 404. The
    # `reverse_lazy` defers resolution until first dispatch — the
    # `next:page_` name comes from the file router's auto-generated
    # name for `shadcn_admin/surfaces/page.py`.
    path(
        "",
        RedirectView.as_view(url=reverse_lazy("next:page_"), permanent=False),
    ),
    path("admin/", include("next.urls")),
]
