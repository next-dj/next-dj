from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.safestring import SafeString
from wiki.markdown_render import render_markdown
from wiki.models import RESERVED_SLUGS, Article

from next.forms import ComponentWidget, Form
from next.pages import context


class ArticleCreateForm(Form):
    slug = django_forms.SlugField(
        max_length=80,
        widget=ComponentWidget("input", placeholder="wiki-slug"),
    )
    title = django_forms.CharField(
        max_length=200,
        widget=ComponentWidget("input", placeholder="Article title"),
    )
    body_md = django_forms.CharField(
        required=False,
        widget=ComponentWidget(
            "textarea",
            placeholder="# Markdown body",
            rows=12,
            markdown_source=True,
        ),
    )

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Persist a new article and redirect to its public URL."""
        article = Article.objects.create(
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
        if Article.objects.filter(slug=slug).exists():
            msg = "Slug already taken by another article."
            raise django_forms.ValidationError(msg)
        return slug


@context("preview_html")
def preview_html(request: HttpRequest) -> SafeString:
    """Render the posted body to seed the preview pane between submissions."""
    return render_markdown(request.POST.get("body_md", ""))
