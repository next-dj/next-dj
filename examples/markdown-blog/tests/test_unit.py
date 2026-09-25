from pathlib import Path

import pytest
from blog import receivers
from blog.loaders import MarkdownTemplateLoader
from blog.markdown_template import (
    post_metadata,
    read_post_body,
    reading_minutes,
    render_markdown,
)
from blog.receivers import _detect_source, loader_hits

from next.pages import Page
from next.pages.signals import template_loaded


class TestRenderMarkdown:
    """`render_markdown` wraps python-markdown with sensible extensions."""

    @pytest.mark.parametrize(
        ("source", "needles"),
        [
            ("# Hi\n\nbody", ("<h1>Hi</h1>", "<p>body</p>")),
            ("```python\nx = 1\n```", ("<code", "x = 1")),
        ],
        ids=["headings-and-paragraphs", "fenced-code"],
    )
    def test_extensions_produce_html(
        self, source: str, needles: tuple[str, ...]
    ) -> None:
        html = render_markdown(source)
        for needle in needles:
            assert needle in html


class TestPostMetadata:
    """`post_metadata` extracts title, URL name and excerpt from a post folder."""

    def test_extracts_title_and_url_name(self, tmp_path: Path) -> None:
        post_dir = tmp_path / "my-post"
        post_dir.mkdir()
        post_md = post_dir / "post.md"
        post_md.write_text("# Something\n\ntext")
        assert post_metadata(post_md) == {
            "slug": "my-post",
            "url_name": "next:page_posts_my_post",
            "title": "Something",
            "excerpt": "text",
        }

    @pytest.mark.parametrize(
        ("source", "excerpt"),
        [
            ("# Title\n\nBody text.", "Body text."),
            ("# Title\n## Sub\nUnder a subheading.", "Under a subheading."),
            ("\n\n# Title\n\n\nAfter blank lines.", "After blank lines."),
            (
                "# T\n\nfirst line\nsecond line\n\nnext paragraph",
                "first line second line",
            ),
            ("# T\n\nSome **bold**, `code` and _em_.", "Some bold, code and em."),
            ("# T\n\n" + " ".join(["word"] * 24), " ".join(["word"] * 24)),
            ("# T\n\n" + " ".join(["word"] * 30), " ".join(["word"] * 24) + "…"),
            ("# Only a heading", ""),
        ],
        ids=[
            "skips-heading",
            "skips-subheading",
            "skips-leading-blanks",
            "stops-at-paragraph-boundary",
            "strips-inline-markup",
            "keeps-exact-limit",
            "truncates-past-limit",
            "empty-without-paragraph",
        ],
    )
    def test_excerpt_is_the_first_plain_paragraph(
        self, tmp_path: Path, source: str, excerpt: str
    ) -> None:
        post_md = tmp_path / "post.md"
        post_md.write_text(source)
        assert post_metadata(post_md)["excerpt"] == excerpt


class TestReadPostBody:
    """`read_post_body` returns the raw Markdown text."""

    def test_returns_full_body(self, tmp_path: Path) -> None:
        post_md = tmp_path / "post.md"
        post_md.write_text("# Head\n\nbody")
        assert read_post_body(post_md) == "# Head\n\nbody"


class TestReadingMinutes:
    """`reading_minutes` estimates at ~200 words per minute."""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [("one two three", 1), ("word " * 500, 2)],
        ids=["short", "long"],
    )
    def test_estimate_scales_with_word_count(self, body: str, expected: int) -> None:
        assert reading_minutes(body) == expected


class TestMarkdownTemplateLoader:
    """`MarkdownTemplateLoader` renders sibling `template.md` as HTML."""

    def test_can_load_true_when_template_md_exists(self, tmp_path: Path) -> None:
        (tmp_path / "template.md").write_text("# hi")
        page_file = tmp_path / "page.py"
        assert MarkdownTemplateLoader().can_load(page_file) is True

    def test_can_load_false_when_missing(self, tmp_path: Path) -> None:
        assert MarkdownTemplateLoader().can_load(tmp_path / "page.py") is False

    def test_load_template_renders_markdown(self, tmp_path: Path) -> None:
        (tmp_path / "template.md").write_text("# hi\n\nbody")
        html = MarkdownTemplateLoader().load_template(tmp_path / "page.py")
        assert "<h1>hi</h1>" in (html or "")

    def test_load_template_returns_none_on_decode_error(self, tmp_path: Path) -> None:
        md_file = tmp_path / "template.md"
        md_file.write_bytes(b"\xff\xfe invalid utf-8")
        assert MarkdownTemplateLoader().load_template(tmp_path / "page.py") is None

    def test_source_path_returns_sibling_when_exists(self, tmp_path: Path) -> None:
        md_file = tmp_path / "template.md"
        md_file.write_text("# x")
        assert MarkdownTemplateLoader().source_path(tmp_path / "page.py") == md_file

    def test_source_path_none_when_missing(self, tmp_path: Path) -> None:
        assert MarkdownTemplateLoader().source_path(tmp_path / "page.py") is None


class TestReceivers:
    """`blog.receivers` observes the `template_loaded` signal."""

    def setup_method(self) -> None:
        """Clear the loader hits map before each test."""
        receivers._loader_hits.clear()

    @pytest.mark.parametrize(
        ("siblings", "expected"),
        [
            (("template.md",), "template.md"),
            (("template.djx",), "template.djx"),
            ((), "page.py"),
        ],
        ids=["markdown", "djx", "inline"],
    )
    def test_detect_source_names_the_backing_file(
        self, tmp_path: Path, siblings: tuple[str, ...], expected: str
    ) -> None:
        for name in siblings:
            (tmp_path / name).write_text("x")
        assert _detect_source(tmp_path / "page.py").startswith(expected)

    def test_on_template_loaded_records_hit(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        (tmp_path / "template.djx").write_text("<p>x</p>")
        template_loaded.send(sender=Page, file_path=page_file)
        hits = loader_hits()
        assert hits[str(page_file)].startswith("template.djx")
