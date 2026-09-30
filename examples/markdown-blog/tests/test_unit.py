import datetime
from pathlib import Path

import pytest
from blog import receivers
from blog.loaders import MarkdownTemplateLoader
from blog.markdown_template import render_markdown
from blog.posts import Post, all_posts, load_post, parse_post, reading_minutes
from blog.receivers import _detect_source, loader_hits
from django.http import Http404

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


FRONT = "---\ntitle: Something\nauthor: Ada\ndate: 2026-01-02\n---\n"


class TestParsePost:
    """`parse_post` reads the front matter and keeps the body as Markdown."""

    def test_reads_every_front_matter_field(self) -> None:
        post = parse_post(
            "my-post",
            "---\ntitle: Something\nauthor: Ada\ndate: 2026-01-02\n"
            "updated: 2026-02-03\ndescription: Said plainly.\n"
            "keywords: one, two,, three\n---\n\nbody text\n",
        )
        assert post == Post(
            slug="my-post",
            title="Something",
            author="Ada",
            published=datetime.date(2026, 1, 2),
            updated=datetime.date(2026, 2, 3),
            description="Said plainly.",
            keywords=("one", "two", "three"),
            body="body text",
        )
        assert post.modified == datetime.date(2026, 2, 3)

    def test_an_unedited_post_was_modified_when_published(self) -> None:
        post = parse_post("p", FRONT + "text")
        assert post.updated is None
        assert post.modified == datetime.date(2026, 1, 2)
        assert post.keywords == ()

    @pytest.mark.parametrize(
        ("body", "description"),
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
    def test_without_a_description_the_first_paragraph_stands_in(
        self, body: str, description: str
    ) -> None:
        assert parse_post("p", FRONT + body).description == description


class TestLoadPost:
    """`load_post` reads a file of `POSTS_DIR` and `all_posts` lists them."""

    def test_an_unknown_slug_is_not_found(self) -> None:
        with pytest.raises(Http404):
            load_post("missing")

    def test_every_post_is_listed_newest_first(self) -> None:
        assert [post.slug for post in all_posts()] == ["hello-world", "welcome"]

    def test_a_post_links_its_own_address(self) -> None:
        assert load_post("welcome").path == "/posts/welcome/"


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

    def test_load_template_renders_markdown_as_a_prose_block(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "template.md").write_text("# hi\n\nbody")
        html = MarkdownTemplateLoader().load_template(tmp_path / "page.py")
        assert html == (
            '<article class="prose prose-slate max-w-none text-foreground">'
            "<h1>hi</h1>\n<p>body</p></article>"
        )

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
