from typing import ClassVar

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from myapp.models import Post

from next import forms
from next.pages import context


@context("post")
def get_post_for_edit(request: HttpRequest, id: int) -> Post:  # noqa: A002
    post = get_object_or_404(Post, pk=id)
    if post.author_id != request.user.id:
        raise PermissionDenied
    return post


class PostEditForm(forms.ModelForm):
    """Edit an existing post."""

    class Meta:
        model = Post
        fields: ClassVar[list[str]] = ["title", "content"]
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 10}),
        }

    @classmethod
    def get_initial(cls, id: int) -> Post | dict:  # noqa: A002
        """Load post by id for editing, or empty dict if missing."""
        try:
            return Post.objects.get(pk=id)
        except Post.DoesNotExist:
            return {}


@forms.action("update_post", form_class=PostEditForm)
def update_post_handler(
    form: PostEditForm,
    request: HttpRequest,
    id: int,  # noqa: A002
) -> HttpResponseRedirect | HttpResponseForbidden:
    if form.instance.author_id != request.user.id:
        return HttpResponseForbidden("Permission denied.")
    form.save()
    messages.success(request, "Changes saved.")
    return HttpResponseRedirect(f"/posts/{id}/details/")
