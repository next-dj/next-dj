import datetime
from dataclasses import dataclass
from pathlib import Path

from django.http import Http404
from django.templatetags.static import static
from django.urls import reverse
from django.utils.safestring import SafeString
from django.utils.text import Truncator

from .markdown_template import render_markdown


POSTS_DIR = Path(__file__).parent / "posts"
FENCE = "---\n"
WPM = 200
EXCERPT_WORDS = 24
INLINE_MARKUP = str.maketrans("", "", "*`_")
SOCIAL_IMAGE = static("blog/opengraph-image.png")


@dataclass(frozen=True, slots=True)
class Post:
    """One Markdown post, its front matter parsed and its body kept as source."""

    slug: str
    title: str
    author: str
    published: datetime.date
    updated: datetime.date | None
    description: str
    keywords: tuple[str, ...]
    body: str

    @property
    def modified(self) -> datetime.date:
        """Return the day of the last edit, the publication day for an unedited post."""
        return self.updated or self.published

    @property
    def path(self) -> str:
        """Return the address of the post."""
        return reverse("next:page_posts_slug", kwargs={"slug": self.slug})

    @property
    def html(self) -> SafeString:
        """Return the body rendered to HTML."""
        return SafeString(render_markdown(self.body))

    @property
    def reading_minutes(self) -> int:
        """Estimate the reading time in whole minutes at about 200 words a minute."""
        return reading_minutes(self.body)


def reading_minutes(text: str) -> int:
    """Estimate the reading time in whole minutes at `WPM` words a minute."""
    return max(1, round(len(text.split()) / WPM))


def excerpt(body: str) -> str:
    """Return the first paragraph after any heading as plain text, trimmed."""
    paragraph: list[str] = []
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        if not line.strip():
            if paragraph:
                break
            continue
        paragraph.append(line.strip().translate(INLINE_MARKUP))
    return Truncator(" ".join(paragraph)).words(EXCERPT_WORDS)


def parse_post(slug: str, text: str) -> Post:
    """Read one post, a `key: value` front matter between two `---` lines on top."""
    _, head, body = text.split(FENCE, 2)
    fields = {
        key.strip(): value.strip()
        for key, _, value in (line.partition(":") for line in head.splitlines())
    }
    updated = fields.get("updated")
    keywords = (word.strip() for word in fields.get("keywords", "").split(","))
    return Post(
        slug=slug,
        title=fields["title"],
        author=fields["author"],
        published=datetime.date.fromisoformat(fields["date"]),
        updated=datetime.date.fromisoformat(updated) if updated else None,
        description=fields.get("description") or excerpt(body),
        keywords=tuple(filter(None, keywords)),
        body=body.strip(),
    )


def load_post(slug: str) -> Post:
    """Return the post the slug names, a 404 when no Markdown file carries it."""
    path = POSTS_DIR / f"{slug}.md"
    if not path.is_file():
        raise Http404(slug)
    return parse_post(slug, path.read_text(encoding="utf-8"))


def all_posts() -> list[Post]:
    """Return every post, the newest first."""
    posts = (
        parse_post(path.stem, path.read_text(encoding="utf-8"))
        for path in POSTS_DIR.glob("*.md")
    )
    return sorted(posts, key=lambda post: post.published, reverse=True)
