from typing import Any, ClassVar

from django.http import HttpRequest, HttpResponse
from library import services
from library.models import Book, Chapter

from next import context, page
from next.deps import DObject
from next.forms import ComponentWidget, ModelForm
from next.pages import MetadataDict
from next.partial import Patches
from next.urls import page_reverse


MANUSCRIPT_ZONE = "manuscript"


class AddChapterForm(ModelForm):
    class Meta:
        model = Chapter
        fields: ClassVar = ["title", "word_count"]
        widgets: ClassVar = {
            "title": ComponentWidget("input"),
            "word_count": ComponentWidget("input", type="number"),
        }

    @classmethod
    def get_form_kwargs(cls, book: DObject[Book]) -> dict[str, Any]:
        """Bind a new chapter of the book the URL names."""
        return {"instance": Chapter(book=book)}

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Append the chapter and morph the zone, a 303 back without the runtime."""
        services.append_chapter(self.instance)
        return Patches(request).morph_zone(MANUSCRIPT_ZONE).response()


@context("book")
def book(book: DObject[Book]) -> Book:
    """Return the book the URL names, the row the `proxy.py` rule already loaded."""
    return book


@context("outline_url")
def outline_url(book: DObject[Book]) -> str:
    """Return the outline of the book, the page that links here."""
    return page_reverse("library/book/[int:book]/outline", book=book.pk)


@context("manuscript", zone=MANUSCRIPT_ZONE)
def manuscript(book: DObject[Book]) -> dict[str, Any]:
    """Return the chapters of the book in reading order with their total length."""
    return {"chapters": list(book.chapters.all()), "words": book.chapters.total_words()}


@page.metadata
def manuscript_meta(book: DObject[Book]) -> MetadataDict:
    """Title the page after the book and label its crumb, the trail reads its tab."""
    return {"title": book.title, "breadcrumb": "Manuscript"}
