from typing import ClassVar

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from wiki.deps import DArticle
from wiki.models import RESERVED_SLUGS, Article
from wiki.services import slug_taken, update_article

from next import context, page
from next.forms import ComponentWidget, ModelForm, PermissionOutcome
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Edit article", "robots": {"index": False}}

page.public()


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
        article = update_article(
            article_id=self.instance.pk,
            slug=self.cleaned_data["slug"],
            title=self.cleaned_data["title"],
            body_md=self.cleaned_data["body_md"],
        )
        return HttpResponseRedirect(article.url)

    def clean_slug(self) -> str:
        """Reject reserved prefixes and slugs taken by another article."""
        slug = self.cleaned_data["slug"]
        if slug in RESERVED_SLUGS:
            msg = "This slug collides with a file route."
            raise django_forms.ValidationError(msg)
        if slug_taken(slug, exclude_pk=self.instance.pk):
            msg = "Slug already taken by another article."
            raise django_forms.ValidationError(msg)
        return slug


@context("article")
def article(item: DArticle[Article]) -> Article:
    """Inject the article addressed by the URL slug for the template."""
    return item
