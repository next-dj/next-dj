from django.urls import path

from tests.load.views import stream


urlpatterns = [path("stream/<str:name>/", stream)]
