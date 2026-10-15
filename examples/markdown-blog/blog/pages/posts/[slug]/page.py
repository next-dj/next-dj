from blog.posts import SOCIAL_IMAGE, Post, load_post

from next import context, page
from next.pages import MetadataDict
from next.pages.metadata import absolute_url


@context("article")
def article(slug: str) -> Post:
    """Read the post the URL names, parsed once for the body, head and layout."""
    return load_post(slug)


@context("post", serialize=True)
def post(article: Post) -> dict[str, str]:
    """Hand the share button the two fields it reads from `window.Next.context`."""
    return {"slug": article.slug, "title": article.title}


@page.metadata
def post_meta(article: Post) -> MetadataDict:
    """Describe the post from its front matter, for search and for social cards."""
    return {
        "title": article.title,
        "description": article.description,
        "keywords": list(article.keywords),
        "og": {
            "type": "article",
            "article": {
                "published_time": article.published,
                "modified_time": article.modified,
                "authors": [article.author],
                "tags": list(article.keywords),
            },
        },
        "jsonld": [
            {
                "@type": "BlogPosting",
                "headline": article.title,
                "datePublished": article.published,
                "dateModified": article.modified,
                "image": [absolute_url(SOCIAL_IMAGE)],
                "author": [{"@type": "Person", "name": article.author}],
            }
        ],
    }
