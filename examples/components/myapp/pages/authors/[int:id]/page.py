from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from myapp.models import Post

from next.pages import context


User = get_user_model()


@context("author_user")
def get_author_user(id: int) -> User:  # noqa: A002
    return get_object_or_404(User, pk=id)


@context("author_posts")
def get_author_posts(id: int) -> QuerySet[Post]:  # noqa: A002
    return (
        Post.objects.filter(author_id=id)
        .select_related("author")
        .order_by(
            "-created_at",
        )
    )
