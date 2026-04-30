from typing import Any

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from wiki.models import RESERVED_SLUGS, Article

from next.forms import Form, action
from next.pages import context


INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-400"
)
TEXTAREA_CLASS = INPUT_CLASS + " min-h-[260px] font-mono"


class ArticleCreateForm(Form):
    slug = django_forms.SlugField(
        max_length=80,
        widget=django_forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "wiki-slug"},
        ),
    )
    title = django_forms.CharField(
        max_length=200,
        widget=django_forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "Article title"},
        ),
    )
    body_md = django_forms.CharField(
        required=False,
        widget=django_forms.Textarea(
            attrs={
                "class": TEXTAREA_CLASS,
                "placeholder": "# Markdown body",
                "data-markdown-source": "true",
            },
        ),
    )

    @classmethod
    def get_initial(cls) -> dict[str, Any]:
        """Empty initial state for the create form."""
        return {}

    def clean_slug(self) -> str:
        """Reject reserved prefixes and existing slugs."""
        slug = self.cleaned_data["slug"]
        if slug in RESERVED_SLUGS:
            msg = "This slug collides with a file route."
            raise django_forms.ValidationError(msg)
        if Article.objects.filter(slug=slug).exists():
            msg = "Slug already taken by another article."
            raise django_forms.ValidationError(msg)
        return slug


@context("body")
def preview_body(request: HttpRequest) -> str:
    """Body to seed the markdown preview pane between submissions."""
    return request.POST.get("body_md", "")


@action("article_create", namespace="wiki", form_class=ArticleCreateForm)
def article_create(form: ArticleCreateForm) -> HttpResponseRedirect:
    """Persist a new article and redirect to its public URL."""
    article = Article.objects.create(
        slug=form.cleaned_data["slug"],
        title=form.cleaned_data["title"],
        body_md=form.cleaned_data.get("body_md", ""),
    )
    return HttpResponseRedirect(article.url)
