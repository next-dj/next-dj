from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from wiki.models import RESERVED_SLUGS
from wiki.services import create_article, slug_taken

from next import page
from next.forms import ComponentWidget, Form
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Create an article", "robots": {"index": False}}

page.public()


class ArticleCreateForm(Form):
    slug = django_forms.SlugField(
        max_length=80, widget=ComponentWidget("input", placeholder="wiki-slug")
    )
    title = django_forms.CharField(
        max_length=200, widget=ComponentWidget("input", placeholder="Article title")
    )
    body_md = django_forms.CharField(
        required=False,
        widget=ComponentWidget(
            "markdown_textarea", placeholder="# Markdown body", rows=12
        ),
    )

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Persist a new article and redirect to its public URL."""
        article = create_article(
            slug=self.cleaned_data["slug"],
            title=self.cleaned_data["title"],
            body_md=self.cleaned_data.get("body_md", ""),
        )
        return HttpResponseRedirect(article.url)

    def clean_slug(self) -> str:
        """Reject reserved prefixes and existing slugs."""
        slug = self.cleaned_data["slug"]
        if slug in RESERVED_SLUGS:
            msg = "This slug collides with a file route."
            raise django_forms.ValidationError(msg)
        if slug_taken(slug):
            msg = "Slug already taken by another article."
            raise django_forms.ValidationError(msg)
        return slug
