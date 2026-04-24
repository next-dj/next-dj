from pathlib import Path

from blog.markdown_template import post_metadata

from next.pages import context


POSTS_DIR = Path(__file__).parent / "posts"


@context("posts")
def posts() -> list[dict[str, str]]:
    return sorted(
        (post_metadata(p) for p in POSTS_DIR.glob("*/template.md")),
        key=lambda m: m["slug"],
    )
