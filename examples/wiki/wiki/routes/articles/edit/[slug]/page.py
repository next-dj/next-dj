from typing import ClassVar

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from wiki.models import RESERVED_SLUGS, Article
from wiki.providers import DArticle

from next import context
from next.forms import ComponentWidget, ModelForm, PermissionOutcome


class ArticleEditForm(ModelForm):
    class Meta:
        model = Article
        fields: ClassVar = ["slug", "title", "body_md"]
        instance_from_url = "slug"
        widgets: ClassVar = {
            "slug": ComponentWidget("input"),
            "title": ComponentWidget("input"),
            "body_md": ComponentWidget("markdown_textarea", rows=12),
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
