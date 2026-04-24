from pathlib import Path

from blog.markdown_template import (
    post_metadata,
    read_post_body,
    reading_minutes,
    render_markdown,
)

from next.pages import context


_POST = Path(__file__).parent / "post.md"


template = '<div class="prose prose-slate max-w-none">{{ post_html|safe }}</div>'


@context("post", serialize=True)
def _post() -> dict[str, str]:
    return post_metadata(_POST)


@context("post_html")
def _html() -> str:
    return render_markdown(read_post_body(_POST))


@context("reading_minutes")
def _read() -> int:
    return reading_minutes(read_post_body(_POST))
