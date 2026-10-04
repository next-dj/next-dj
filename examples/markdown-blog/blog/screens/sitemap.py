from collections.abc import Iterator

from blog.posts import all_posts

from next.seo import SitemapEntry, sitemap


changefreq = "weekly"


@sitemap.items("posts/[slug]")
def posts() -> Iterator[SitemapEntry]:
    """One entry per Markdown file, dated by its last edit."""
    for post in all_posts():
        yield SitemapEntry(kwargs={"slug": post.slug}, lastmod=post.modified)
