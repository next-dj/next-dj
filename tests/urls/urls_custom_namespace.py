from django.urls import include, path


urlpatterns = [path("dash/", include(("next.urls", "next"), namespace="dashboard"))]
