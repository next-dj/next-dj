from typing import ClassVar

from django.contrib import admin

from library.models import Author, Book, Chapter, Tag


class ChapterInline(admin.TabularInline):
    model = Chapter
    extra = 1
    fields = ("number", "title", "word_count")


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "born_in")
    search_fields = ("full_name", "email")
    list_filter = ("born_in",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar = {"slug": ("name",)}


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "status", "published_at", "price")
    list_filter = ("status", "tags")
    search_fields = ("title", "summary", "author__full_name")
    autocomplete_fields = ("author",)
    filter_horizontal = ("tags",)
    inlines: ClassVar = [ChapterInline]
    date_hierarchy = "published_at"


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ("book", "number", "title", "word_count")
    search_fields = ("title", "book__title")
    list_filter = ("book",)
