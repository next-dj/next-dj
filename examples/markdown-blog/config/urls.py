from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path


urlpatterns = [
    path("", include("next.seo.urls")),
    *i18n_patterns(path("", include("next.urls")), prefix_default_language=False),
]
