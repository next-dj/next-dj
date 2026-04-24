from django.urls import include, path
from shortener.views import redirect_slug


urlpatterns = [
    path("s/<slug:slug>/", redirect_slug, name="slug_redirect"),
    path("", include("next.urls")),
]
