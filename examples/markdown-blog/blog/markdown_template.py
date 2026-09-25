from pathlib import Path

import markdown
from django.utils.text import Truncator


WPM = 200
EXCERPT_WORDS = 24
INLINE_MARKUP = str.maketrans("", "", "*`_")


def render_markdown(text: str) -> str:
    """Convert a Markdown document body to HTML."""
    return markdown.markdown(text, extensions=["fenced_code"])


def read_post_body(post_path: Path) -> str:
    """Return the raw Markdown text for a post."""
    return post_path.read_text(encoding="utf-8")


def _excerpt(lines: list[str]) -> str:
    """Return the first paragraph after the heading as plain text, trimmed."""
    paragraph: list[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        if not line.strip():
            if paragraph:
                break
            continue
        paragraph.append(line.strip().translate(INLINE_MARKUP))
    return Truncator(" ".join(paragraph)).words(EXCERPT_WORDS)


def post_metadata(post_path: Path) -> dict[str, str]:
    """Return slug, URL name, title and excerpt read from the Markdown source."""
    lines = read_post_body(post_path).splitlines()
    heading = next((line for line in lines if line.startswith("# ")), "")
    slug = post_path.parent.name
    title = heading.removeprefix("# ").strip() or slug.replace("-", " ").title()
    return {
        "slug": slug,
        "url_name": f"next:page_posts_{slug.replace('-', '_')}",
        "title": title,
        "excerpt": _excerpt(lines),
    }


def reading_minutes(text: str) -> int:
    """Estimate reading time in whole minutes at ~200 wpm."""
    words = len(text.split())
    return max(1, round(words / WPM))
