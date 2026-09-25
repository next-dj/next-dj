from django.urls import include, path


urlpatterns = [
    path("", include("next.seo.urls")),
    path("prefix/", include("next.urls")),
]
