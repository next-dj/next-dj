from typing import ClassVar

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponseRedirect
from myapp.models import Post

from next import forms


class PostCreateForm(forms.ModelForm):
    """Create a new blog post (author set in handler)."""

    class Meta:
        model = Post
        fields: ClassVar[list[str]] = ["title", "content"]
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 10}),
        }

    @classmethod
    def get_initial(cls, _request: HttpRequest) -> dict:
        """Empty initial for a new post."""
        return {}


@forms.action("create_post", form_class=PostCreateForm)
def create_post_handler(
    form: PostCreateForm, request: HttpRequest
) -> HttpResponseRedirect:
    if not request.user.is_authenticated:
        return HttpResponseRedirect(settings.LOGIN_URL)
    post = form.save(commit=False)
    post.author = request.user
    post.save()
    messages.success(request, "Post created.")
    return HttpResponseRedirect("/")
