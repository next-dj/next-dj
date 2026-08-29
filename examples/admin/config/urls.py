from django.contrib import admin
from django.urls import include, path, reverse_lazy
from django.views.generic import RedirectView


admin.autodiscover()

urlpatterns = [
    # Send bare `/` to the dashboard so a fresh visit does not 404. `next:page_`
    # is the file router's generated name for `shadcn_admin/surfaces/page.py`.
    path("", RedirectView.as_view(url=reverse_lazy("next:page_"), permanent=False)),
    path("admin/", include("next.urls")),
]
