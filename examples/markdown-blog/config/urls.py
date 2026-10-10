from blog.feeds import LatestPostsFeed
from django.urls import include, path


urlpatterns = [
    path("feed.xml", LatestPostsFeed(), name="feed"),
    path("", include("next.urls")),
]
