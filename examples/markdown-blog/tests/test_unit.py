from pathlib import Path

from blog.markdown_template import (
    post_metadata,
    read_post_body,
    reading_minutes,
    render_markdown,
)


class TestRenderMarkdown:
    """`render_markdown` wraps python-markdown with sensible extensions."""

    def test_headings_and_paragraphs(self) -> None:
        html = render_markdown("# Hi\n\nbody")
        assert "<h1>Hi</h1>" in html
        assert "<p>body</p>" in html

    def test_fenced_code_block(self) -> None:
        html = render_markdown("```python\nx = 1\n```")
        assert "<code" in html
        assert "x = 1" in html


class TestPostMetadata:
    """`post_metadata` extracts title and URL name from a post folder."""

    def test_extracts_title_and_url_name(self, tmp_path: Path) -> None:
        post_dir = tmp_path / "my-post"
        post_dir.mkdir()
        post_md = post_dir / "post.md"
        post_md.write_text("# Something\n\ntext")
        assert post_metadata(post_md) == {
            "slug": "my-post",
            "url_name": "next:page_posts_my_post",
            "title": "Something",
        }


class TestReadPostBody:
    """`read_post_body` returns the raw Markdown text."""

    def test_returns_full_body(self, tmp_path: Path) -> None:
        post_md = tmp_path / "post.md"
        post_md.write_text("# Head\n\nbody")
        assert read_post_body(post_md) == "# Head\n\nbody"


class TestReadingMinutes:
    """`reading_minutes` estimates at ~200 words per minute."""

    def test_short_text_is_one_minute(self) -> None:
        assert reading_minutes("one two three") == 1

    def test_long_text_rounds(self) -> None:
        body = "word " * 500
        assert reading_minutes(body) == 2
