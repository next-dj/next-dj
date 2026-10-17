from typing import Any, ClassVar

from django.http import HttpRequest, HttpResponse
from library import services
from library.models import Book, Chapter
from shadcn_admin import utils

from next import context, page
from next.deps import DObject
from next.forms import ComponentWidget, ModelForm
from next.pages import MetadataDict
from next.partial import Patches


OUTLINE_ZONE = "outline"


class AddChapterForm(ModelForm):
    class Meta:
        model = Chapter
        fields: ClassVar = ["title", "word_count"]
        widgets: ClassVar = {
            "title": ComponentWidget("input"),
            "word_count": ComponentWidget("input", type="number"),
        }

    def on_valid(self, request: HttpRequest, book: DObject[Book]) -> HttpResponse:
        """Append the chapter and morph the outline, a 303 back without the runtime."""
        services.append_chapter(self.instance, book)
        return Patches(request).morph_zone(OUTLINE_ZONE).response()


@context("book")
def book(book: DObject[Book]) -> Book:
    """Return the book the URL names, the row the `proxy.py` rule already loaded."""
    return book


@context("change_url")
def change_url(book: DObject[Book]) -> str:
    """Return the change view of the book, the page that links here."""
    return utils.change_url("library", "book", book.pk)


@context("outline", zone=OUTLINE_ZONE)
def outline(book: DObject[Book]) -> dict[str, Any]:
    """Return the chapters of the book in reading order with their total length."""
    chapters = list(book.chapters.all())
    return {"chapters": chapters, "words": sum(c.word_count for c in chapters)}


@page.metadata
def outline_meta(book: DObject[Book]) -> MetadataDict:
    """Title the page after the book and label its crumb, the trail reads its tab."""
    return {"title": book.title, "breadcrumb": "Outline"}
