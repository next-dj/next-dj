import copy
from typing import ClassVar

from django.contrib import admin
from django.db.models import ForeignKey, QuerySet
from django.forms import ModelChoiceField
from django.http import HttpRequest
from shadcn_admin.admin import ParentRowPermissions, RowPermissionAdmin, may

from library.models import Author, Book, Chapter, Tag
from next.urls import page_reverse


class ChapterInline(ParentRowPermissions, admin.TabularInline):
    model = Chapter
    extra = 1
    fields = ("number", "title", "word_count")


@admin.register(Author)
class AuthorAdmin(RowPermissionAdmin):
    list_display = ("full_name", "email", "born_in")
    search_fields = ("full_name", "email")
    list_filter = ("born_in",)


@admin.register(Tag)
class TagAdmin(RowPermissionAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar = {"slug": ("name",)}


@admin.register(Book)
class BookAdmin(RowPermissionAdmin):
    list_display = ("title", "author", "status", "is_featured", "published_at", "price")
    list_filter = ("status", "tags", "is_featured")
    search_fields = ("title", "summary", "author__full_name")
    autocomplete_fields = ("author",)
    filter_horizontal = ("tags",)
    list_per_page = 12
    inlines: ClassVar = [ChapterInline]
    date_hierarchy = "published_at"
    actions: ClassVar = ["mark_as_published"]

    @admin.action(
        description="Mark selected %(verbose_name_plural)s as published",
        permissions=["change"],
    )
    def mark_as_published(self, request: HttpRequest, queryset: QuerySet[Book]) -> None:
        """Publish the selected books the user may change now and once published."""
        allowed = [b.pk for b in queryset if self._may_publish(request, b)]
        updated = queryset.filter(pk__in=allowed).update(status=Book.PUBLISHED)
        self.message_user(request, f"{updated} book(s) marked as published.")

    def get_object_links(
        self, request: HttpRequest, obj: Book
    ) -> list[tuple[str, str]]:
        """Link the outline of a book the user may change, the right that page asks."""
        if not self.has_change_permission(request, obj):
            return []
        return [
            ("Outline", page_reverse("library/book/[int:book]/outline", book=obj.pk))
        ]

    def _may_publish(self, request: HttpRequest, book: Book) -> bool:
        published = copy.copy(book)
        published.status = Book.PUBLISHED
        return self.has_change_permission(request, book) and self.has_change_permission(
            request, published
        )


@admin.register(Chapter)
class ChapterAdmin(RowPermissionAdmin):
    list_display = ("book", "number", "title", "word_count")
    search_fields = ("title", "book__title")
    list_filter = ("book",)

    def formfield_for_foreignkey(
        self, db_field: ForeignKey, request: HttpRequest, **kwargs
    ) -> ModelChoiceField:
        """Offer only the books the user may change as a chapter's book."""
        if db_field.name == "book":
            editable = [
                b.pk for b in Book.objects.all() if may(request, "change", Book, b)
            ]
            kwargs["queryset"] = Book.objects.filter(pk__in=editable)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
