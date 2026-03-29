from django.shortcuts import get_object_or_404
from myapp.models import Post

from next.pages import context


@context("post")
def get_post(id: int) -> Post:  # noqa: A002
    return get_object_or_404(Post.objects.select_related("author"), pk=id)


@context("recommended")
def get_recommended(id: int) -> list[Post]:  # noqa: A002
    return list(
        Post.objects.select_related("author").exclude(pk=id).order_by("-created_at")[:3]
    )
