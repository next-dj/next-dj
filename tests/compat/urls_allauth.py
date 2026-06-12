from django.urls import include, path


urlpatterns = [
    path("accounts/", include("allauth.urls")),
    path("", include("next.urls")),
]
