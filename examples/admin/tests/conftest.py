import pytest
from django.utils.text import slugify
from library.demo import seed_demo
from library.models import Author, Book, Chapter, Tag


CHAPTER_ROWS = ((1, "Intro", 100), (2, "Rising", 200))


@pytest.fixture()
def demo_data(db):
    """Seed the shipped demo catalog for tests that read a whole changelist."""
    seed_demo()


@pytest.fixture()
def make_tag(db):
    """Build a tag, deriving the slug from the name unless one is given."""

    def make(name="Fantasy", **fields):
        fields.setdefault("slug", slugify(name))
        return Tag.objects.create(name=name, **fields)

    return make


@pytest.fixture()
def make_author(db):
    """Build an author whose every remaining column is overridable."""

    def make(full_name="A. Author", **fields):
        return Author.objects.create(full_name=full_name, **fields)

    return make


@pytest.fixture()
def make_book(make_author):
    """Build a book, attaching a throwaway author unless the caller names one."""

    def make(title="Book", **fields):
        fields.setdefault("author", make_author())
        return Book.objects.create(title=title, **fields)

    return make


@pytest.fixture()
def make_chapter(db):
    """Build a chapter of `book` from the canonical first-row defaults."""

    def make(book, number=1, title="Intro", word_count=100):
        return Chapter.objects.create(
            book=book, number=number, title=title, word_count=word_count
        )

    return make


@pytest.fixture()
def author(make_author):
    return make_author()


@pytest.fixture()
def book(make_book):
    return make_book()


@pytest.fixture()
def book_with_one_chapter(make_book, make_chapter):
    target = make_book()
    return target, make_chapter(target, *CHAPTER_ROWS[0])


@pytest.fixture()
def book_with_two_chapters(make_book, make_chapter):
    target = make_book()
    first, second = (make_chapter(target, *row) for row in CHAPTER_ROWS)
    return target, first, second
