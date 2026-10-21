from blog.posts import Post

from next import page
from next.pages import MetadataDict


@page.metadata
def print_meta(article: Post) -> MetadataDict:
    """Title the print sheet after the post and keep it out of the index."""
    return {
        "title": article.title,
        "description": article.description,
        "robots": "noindex",
    }
