from typing import Any

from library.models import Book

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict
from next.urls import page_reverse


@context("book")
def book(book: DObject[Book]) -> Book:
    """Return the book the URL names, loaded through `visible_to(scope)`."""
    return book


@context("change_url")
def change_url(book: DObject[Book]) -> str:
    """Return the change view of the book, the page that links here."""
    return page_reverse(
        "[str:app_label]/[str:model_name]/[int:pk]/change",
        app_label="library",
        model_name="book",
        pk=book.pk,
    )


@context("manuscript_url")
def manuscript_url(book: DObject[Book]) -> str:
    """Return the manuscript of the book, where its chapters are appended."""
    return page_reverse("library/book/[int:book]/manuscript", book=book.pk)


@context("outline", zone="outline")
def outline(book: DObject[Book]) -> dict[str, Any]:
    """Return the chapters of the book in reading order with their total length."""
    return {"chapters": list(book.chapters.all()), "words": book.chapters.total_words()}


@page.metadata
def outline_meta(book: DObject[Book]) -> MetadataDict:
    """Title the page after the book and label its crumb, the trail reads its tab."""
    return {"title": book.title, "breadcrumb": "Outline"}
