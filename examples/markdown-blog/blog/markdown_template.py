from pathlib import Path

import markdown


WPM = 200


def render_markdown(text: str) -> str:
    """Convert a Markdown document body to HTML."""
    return markdown.markdown(text, extensions=["fenced_code"])


def read_post_body(post_path: Path) -> str:
    """Return the raw Markdown text for a post."""
    return post_path.read_text(encoding="utf-8")


def post_metadata(post_path: Path) -> dict[str, str]:
    """Return slug, URL name, and title extracted from the first `# …` line."""
    body = read_post_body(post_path)
    first_line = body.splitlines()[0]
    slug = post_path.parent.name
    return {
        "slug": slug,
        "url_name": f"next:page_posts_{slug.replace('-', '_')}",
        "title": first_line.removeprefix("# ").strip(),
    }


def reading_minutes(text: str) -> int:
    """Estimate reading time in whole minutes at ~200 wpm."""
    words = len(text.split())
    return max(1, round(words / WPM))
