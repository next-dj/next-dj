from pathlib import Path

from blog.markdown_template import post_metadata, read_post_body, reading_minutes

from next.pages import context


_POST = Path(__file__).parent / "template.md"


@context("post", serialize=True)
def _post() -> dict[str, str]:
    return post_metadata(_POST)


@context("reading_minutes")
def _read() -> int:
    return reading_minutes(read_post_body(_POST))
