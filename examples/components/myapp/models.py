from typing import ClassVar

from django.conf import settings
from django.db import models
from django.urls import reverse


class Post(models.Model):
    """Blog post written by a user."""

    title = models.CharField(max_length=255)
    content = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="posts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        """URL of the post detail page for this instance."""
        return reverse("next:page_posts_int_id_details", kwargs={"id": self.id})
