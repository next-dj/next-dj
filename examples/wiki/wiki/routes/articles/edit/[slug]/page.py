from typing import Any

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from wiki.models import RESERVED_SLUGS, Article
from wiki.providers import DArticle

from next.forms import Form, action
from next.pages import context


INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-400"
)
TEXTAREA_CLASS = INPUT_CLASS + " min-h-[260px] font-mono"


class ArticleEditForm(Form):
    article_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    slug = django_forms.SlugField(
        max_length=80,
        widget=django_forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    title = django_forms.CharField(
        max_length=200,
        widget=django_forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    body_md = django_forms.CharField(
        required=False,
        widget=django_forms.Textarea(
            attrs={
                "class": TEXTAREA_CLASS,
                "data-markdown-source": "true",
            },
        ),
    )

    @classmethod
    def get_initial(
        cls,
        request: HttpRequest,  # noqa: ARG003
        slug: str | None = None,
    ) -> dict[str, Any]:
        """Seed the form from the existing article on GET requests."""
        if slug is None:
            return {}
        article = get_object_or_404(Article, slug=slug)
        return {
            "article_id": article.pk,
            "slug": article.slug,
            "title": article.title,
            "body_md": article.body_md,
        }

    def clean_slug(self) -> str:
        """Reject reserved prefixes and slugs taken by another article."""
        slug = self.cleaned_data["slug"]
        if slug in RESERVED_SLUGS:
            msg = "This slug collides with a file route."
            raise django_forms.ValidationError(msg)
        article_id = self.cleaned_data.get("article_id")
        clash = Article.objects.filter(slug=slug).exclude(pk=article_id).exists()
        if clash:
            msg = "Slug already taken by another article."
            raise django_forms.ValidationError(msg)
        return slug


@context("article")
def article(item: DArticle[Article]) -> Article:
    """Inject the article addressed by the URL slug for the template."""
    return item


@context("body")
def preview_body(request: HttpRequest, item: DArticle[Article]) -> str:
    """Body that feeds the live markdown preview between submissions."""
    posted = request.POST.get("body_md")
    if posted is not None:
        return posted
    return item.body_md


@action("article_edit", namespace="wiki", form_class=ArticleEditForm)
def article_edit(form: ArticleEditForm) -> HttpResponseRedirect:
    """Persist edits to an existing article and redirect to its public URL."""
    article_obj = get_object_or_404(Article, pk=form.cleaned_data["article_id"])
    article_obj.slug = form.cleaned_data["slug"]
    article_obj.title = form.cleaned_data["title"]
    article_obj.body_md = form.cleaned_data.get("body_md", "")
    article_obj.save()
    return HttpResponseRedirect(article_obj.url)
