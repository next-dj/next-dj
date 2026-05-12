from typing import ClassVar

from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(max_length=64, unique=True)

    class Meta:
        ordering: ClassVar = ["name"]

    def __str__(self) -> str:
        return self.name


class Author(models.Model):
    full_name = models.CharField(max_length=128)
    email = models.EmailField(blank=True)
    born_in = models.PositiveIntegerField(null=True, blank=True)
    bio = models.TextField(blank=True)

    class Meta:
        ordering: ClassVar = ["full_name"]

    def __str__(self) -> str:
        return self.full_name


class Book(models.Model):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    STATUS_CHOICES: ClassVar = [
        (DRAFT, "Draft"),
        (PUBLISHED, "Published"),
        (ARCHIVED, "Archived"),
    ]

    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    tags = models.ManyToManyField(Tag, blank=True, related_name="books")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=DRAFT)
    summary = models.TextField(blank=True)
    published_at = models.DateField(null=True, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    class Meta:
        ordering: ClassVar = ["-published_at", "title"]

    def __str__(self) -> str:
        return self.title


class Chapter(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="chapters")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    word_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar = ["book", "number"]
        unique_together: ClassVar = [("book", "number")]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.book} — ch. {self.number}"
