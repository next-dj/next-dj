from django.urls import include, path


urlpatterns = [path("prefix/", include("next.urls"))]
