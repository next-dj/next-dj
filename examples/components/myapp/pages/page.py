from django.core.paginator import Page, Paginator
from django.http import HttpRequest
from myapp.models import Post

from next.pages import context


@context("page")
def get_page_paginated(request: HttpRequest) -> Page:
    """Paginated list of posts (10 per page)."""
    posts = Post.objects.select_related("author").order_by("-created_at")
    paginator = Paginator(posts, 10)
    try:
        page_num = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page_num = 1
    return paginator.get_page(page_num)
