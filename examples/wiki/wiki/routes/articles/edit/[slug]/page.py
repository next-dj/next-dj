from typing import ClassVar

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from wiki.models import RESERVED_SLUGS, Article
from wiki.providers import DArticle

from next.forms import ModelForm
from next.pages import context


INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-400"
)
TEXTAREA_CLASS = INPUT_CLASS + " min-h-[260px] font-mono"


class ArticleEditForm(ModelForm):
    class Meta:
        model = Article
        fields: ClassVar = ["slug", "title", "body_md"]
        instance_from_url = "slug"
        widgets: ClassVar = {
            "slug": django_forms.TextInput(attrs={"class": INPUT_CLASS}),
            "title": django_forms.TextInput(attrs={"class": INPUT_CLASS}),
            "body_md": django_forms.Textarea(
                attrs={"class": TEXTAREA_CLASS, "data-markdown-source": "true"},
            ),
        }

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Persist edits to an existing article and redirect to its public URL."""
        self.save()
        return HttpResponseRedirect(self.instance.url)

    def clean_slug(self) -> str:
        """Reject reserved prefixes and slugs taken by another article."""
        slug = self.cleaned_data["slug"]
        if slug in RESERVED_SLUGS:
            msg = "This slug collides with a file route."
            raise django_forms.ValidationError(msg)
        clash = Article.objects.filter(slug=slug).exclude(pk=self.instance.pk).exists()
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
