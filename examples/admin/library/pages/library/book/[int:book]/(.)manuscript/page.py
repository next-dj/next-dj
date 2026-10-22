from typing import Any

from library.models import Book

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict


page.permission_required("library.view_book", obj=DObject[Book])


@context("peek")
def peek(book: DObject[Book]) -> dict[str, Any]:
    """Return where the draft stands, its length and the chapter written last."""
    chapters = book.chapters.all()
    return {
        "book": book,
        "count": chapters.count(),
        "words": chapters.total_words(),
        "latest": chapters.order_by("-number").first(),
    }


@page.metadata
def peek_meta(book: DObject[Book]) -> MetadataDict:
    """Title the tab after the book while the peek covers its outline."""
    return {"title": f"Manuscript peek · {book.title}"}
