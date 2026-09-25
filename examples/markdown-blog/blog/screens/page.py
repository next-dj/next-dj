from pathlib import Path

from blog.markdown_template import post_metadata

from next import context
from next.pages import MetadataDict


POSTS_DIR = Path(__file__).parent / "posts"

metadata: MetadataDict = {
    "title": "Latest posts",
    "canonical": True,
    "alternates": {"languages": True},
}


@context("posts")
def posts() -> list[dict[str, str]]:
    return sorted(
        (post_metadata(p) for p in POSTS_DIR.glob("*/template.md")),
        key=lambda m: m["slug"],
    )
