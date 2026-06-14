from typing import ClassVar

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from wiki.models import RESERVED_SLUGS, Article
from wiki.providers import DArticle

from next.forms import ComponentWidget, ModelForm, PermissionOutcome
from next.pages import context


class ArticleEditForm(ModelForm):
    class Meta:
        model = Article
        fields: ClassVar = ["slug", "title", "body_md"]
        instance_from_url = "slug"
        widgets: ClassVar = {
            "slug": ComponentWidget("input"),
            "title": ComponentWidget("input"),
            "body_md": ComponentWidget("textarea", rows=12, markdown_source=True),
        }

    def has_object_permission(self) -> PermissionOutcome:
        """Deny edits to a locked article keyed off the bound ``self.instance``."""
        return not self.instance.locked

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
