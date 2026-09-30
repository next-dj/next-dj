import datetime

from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils import timezone

from next.pages.metadata import absolute_url

from .context_processors import SITE_TAGLINE
from .posts import Post, all_posts


class LatestPostsFeed(Feed):
    """RSS for every post, its links absolute on the published origin."""

    title = "next.dj blog"
    description = SITE_TAGLINE

    def link(self) -> str:
        """Return the index the feed stands for."""
        return absolute_url("/")

    def feed_url(self) -> str:
        """Return the address the feed is read from."""
        return absolute_url(reverse("feed"))

    def items(self) -> list[Post]:
        """Return every post, the newest first."""
        return all_posts()

    def item_title(self, item: Post) -> str:
        """Title an entry after its post."""
        return item.title

    def item_description(self, item: Post) -> str:
        """Describe an entry with the description of its post."""
        return item.description

    def item_link(self, item: Post) -> str:
        """Point an entry at its post."""
        return absolute_url(item.path)

    def item_pubdate(self, item: Post) -> datetime.datetime:
        """Date an entry at the start of the day its post came out."""
        day = datetime.datetime.combine(item.published, datetime.time.min)
        return timezone.make_aware(day)

    def item_categories(self, item: Post) -> tuple[str, ...]:
        """Tag an entry with the keywords of its post."""
        return item.keywords
