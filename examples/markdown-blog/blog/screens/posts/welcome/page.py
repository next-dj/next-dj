from pathlib import Path

from blog.markdown_template import post_metadata, read_post_body, reading_minutes

from next import context, page
from next.pages import MetadataDict


_POST = Path(__file__).parent / "template.md"


@context("post", serialize=True)
def post() -> dict[str, str]:
    return post_metadata(_POST)


@context("reading_minutes")
def read() -> int:
    return reading_minutes(read_post_body(_POST))


@page.metadata
def post_meta(post: dict[str, str]) -> MetadataDict:
    """Title and describe the post from its Markdown heading and first paragraph."""
    return {"title": post["title"], "description": post["excerpt"]}
