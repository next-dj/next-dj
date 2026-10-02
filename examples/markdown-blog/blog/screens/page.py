from blog.posts import SOCIAL_IMAGE, Post, all_posts
from django.urls import reverse_lazy

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {
    "title": "Latest posts",
    "breadcrumb": "Home",
    "canonical": True,
    "og": {
        "images": [
            {
                "url": SOCIAL_IMAGE,
                "type": "image/png",
                "width": 1200,
                "height": 630,
                "alt": "A white band with an indigo mark on a slate background",
            }
        ]
    },
    "alternates": {
        "feeds": [{"url": reverse_lazy("feed"), "type": "rss", "title": "next.dj blog"}]
    },
}


@context("posts")
def posts() -> list[Post]:
    """Return every post for the index, the newest first."""
    return all_posts()
