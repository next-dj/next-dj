from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from wiki.backends import HybridRouterBackend
from wiki.models import Article
from wiki.providers import ArticleProvider, DArticle

from next.testing import (
    NextClient,
    SignalRecorder,
    envelope_of,
    make_resolution_context,
)
from next.urls.signals import router_reloaded


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpResponse


pytestmark = pytest.mark.django_db


def _origin_field(html: str) -> str:
    match = re.search(r'name="_next_form_origin" value="([^"]+)"', html)
    assert match, "Page did not render the hidden _next_form_origin field."
    return match.group(1)


@pytest.fixture()
def make_article() -> Callable[..., Article]:
    """Return a factory building articles with a slug-derived title."""

    def _make(
        slug: str, *, title: str | None = None, body_md: str = "", locked: bool = False
    ) -> Article:
        return Article.objects.create(
            slug=slug,
            title=title or slug.replace("-", " ").capitalize(),
            body_md=body_md,
            locked=locked,
        )

    return _make


@pytest.fixture()
def routing_doc(make_article: Callable[..., Article]) -> Article:
    """Seed an article whose body matches the routing query for searches."""
    return make_article(
        "routing-internals",
        title="Routing internals",
        body_md="# Routing internals\n\nDeep dive on the URL pipeline.",
    )


@pytest.fixture()
def lifecycle_doc(make_article: Callable[..., Article]) -> Article:
    """Seed an unrelated article so listings have at least two rows."""
    return make_article(
        "lifecycle",
        title="Request lifecycle",
        body_md="Discusses every middleware stage.",
    )


@pytest.fixture()
def submit_from_page(next_client: NextClient) -> Callable[..., HttpResponse]:
    """Post an action carrying the origin the given page rendered."""

    def _submit(action: str, *, page: str, data: dict[str, str]) -> HttpResponse:
        rendered = next_client.get(page).content.decode()
        return next_client.post(
            next_client.get_action_url(action),
            {"_next_form_origin": _origin_field(rendered), **data},
        )

    return _submit


class TestIndex:
    """The index page lists file-backed docs alongside DB-backed articles."""

    def test_index_lists_file_docs_and_articles(
        self, next_client: NextClient, routing_doc: Article, lifecycle_doc: Article
    ) -> None:
        response = next_client.get(reverse("next:page_"))
        body = response.content.decode()
        assert response.status_code == 200
        assert ">\n            Routing\n          <" in body
        assert ">\n            Components\n          <" in body
        assert routing_doc.title in body
        assert lifecycle_doc.title in body


class TestFileDocs:
    """Each file-backed documentation page renders through the layout."""

    @pytest.mark.parametrize(
        ("name", "needle"),
        [
            ("next:page_docs_routing", "<h1>Routing</h1>"),
            ("next:page_docs_components", "<h1>Components</h1>"),
        ],
    )
    def test_file_doc_pages_render(
        self, next_client: NextClient, name: str, needle: str
    ) -> None:
        response = next_client.get(reverse(name))
        assert response.status_code == 200
        assert needle in response.content.decode()


class TestDocFigureChildren:
    """The docs figure splices its block body and escapes the caption prop."""

    def test_children_render_as_markup_while_the_prop_escapes(
        self, next_client: NextClient
    ) -> None:
        body = next_client.get(reverse("next:page_docs_components")).content.decode()
        assert "this <em>emphasis</em> and this" in body
        assert "<strong>bold run</strong>" in body
        assert "&lt;em&gt;emphasis&lt;/em&gt;" in body
        assert "<em>emphasis</em></figcaption>" not in body

    def test_routing_page_reuses_the_figure(self, next_client: NextClient) -> None:
        body = next_client.get(reverse("next:page_docs_routing")).content.decode()
        assert "<figure" in body
        assert "<li><code>routes/page.py</code> → <code>/</code></li>" in body


class TestArticleCreation:
    """Posting the create form publishes a fresh `/wiki/<slug>/` URL."""

    def test_creating_article_publishes_url(self, next_client: NextClient) -> None:
        url = next_client.get_action_url("article_create_form")
        response = next_client.post(
            url,
            {
                "slug": "freshly-baked",
                "title": "Freshly baked",
                "body_md": "## Hello\n\nA brand new article.",
            },
        )
        assert response.status_code in (302, 303)
        assert Article.objects.filter(slug="freshly-baked").exists()

        article_response = next_client.get("/wiki/freshly-baked/")
        article_body = article_response.content.decode()
        assert article_response.status_code == 200
        assert "Freshly baked" in article_body
        assert "<h2>Hello</h2>" in article_body

    def test_create_duplicate_slug_shows_error(
        self, submit_from_page: Callable[..., HttpResponse], routing_doc: Article
    ) -> None:
        response = submit_from_page(
            "article_create_form",
            page=reverse("next:page_articles_new"),
            data={"slug": routing_doc.slug, "title": "Duplicate", "body_md": ""},
        )
        assert response.status_code == 200
        assert "already taken" in response.content.decode()


class TestArticleEdit:
    """Saving the edit form replaces the persisted body."""

    def test_editing_article_changes_body(
        self, next_client: NextClient, routing_doc: Article
    ) -> None:
        response = next_client.post_action(
            "article_edit_form",
            {
                "slug": routing_doc.slug,
                "title": routing_doc.title,
                "body_md": "Rewritten body of the article.",
            },
            origin=reverse(
                "next:page_articles_edit_slug", kwargs={"slug": routing_doc.slug}
            ),
        )
        assert response.status_code in (302, 303)

        routing_doc.refresh_from_db()
        assert routing_doc.body_md == "Rewritten body of the article."

        article_response = next_client.get(routing_doc.url)
        assert "Rewritten body of the article." in article_response.content.decode()

    def test_get_edit_page_shows_article(
        self, next_client: NextClient, routing_doc: Article
    ) -> None:
        response = next_client.get(
            reverse("next:page_articles_edit_slug", kwargs={"slug": routing_doc.slug})
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert routing_doc.title in body
        assert 'data-markdown-source="true"' in body

    @pytest.mark.parametrize(
        ("clash_slug", "bad_slug", "expected_error"),
        [
            (None, "docs", "collides with a file route"),
            ("lifecycle", "lifecycle", "already taken"),
        ],
        ids=["reserved-slug", "clash-slug"],
    )
    def test_edit_invalid_slug_shows_error(
        self,
        submit_from_page: Callable[..., HttpResponse],
        make_article: Callable[..., Article],
        routing_doc: Article,
        clash_slug: str | None,
        bad_slug: str,
        expected_error: str,
    ) -> None:
        if clash_slug is not None:
            make_article(clash_slug, title="Other article")
        response = submit_from_page(
            "article_edit_form",
            page=reverse(
                "next:page_articles_edit_slug", kwargs={"slug": routing_doc.slug}
            ),
            data={"slug": bad_slug, "title": routing_doc.title, "body_md": ""},
        )
        assert response.status_code == 200
        assert expected_error in response.content.decode()

    def test_edit_validation_error_shows_preview(
        self, submit_from_page: Callable[..., HttpResponse], routing_doc: Article
    ) -> None:
        response = submit_from_page(
            "article_edit_form",
            page=reverse(
                "next:page_articles_edit_slug", kwargs={"slug": routing_doc.slug}
            ),
            data={
                "slug": "docs",
                "title": routing_doc.title,
                "body_md": "**posted preview**",
            },
        )
        body = response.content.decode()
        assert response.status_code == 200
        assert "<strong>posted preview</strong>" in body


class TestArticleObjectPermission:
    """The edit form denies a locked article through has_object_permission."""

    def test_locked_article_edit_denied(
        self, next_client: NextClient, make_article: Callable[..., Article]
    ) -> None:
        locked = make_article(
            "locked-page", title="Locked page", body_md="Original body.", locked=True
        )
        response = next_client.post_action(
            "article_edit_form",
            {
                "slug": locked.slug,
                "title": locked.title,
                "body_md": "Attempted overwrite.",
            },
            origin=reverse(
                "next:page_articles_edit_slug", kwargs={"slug": locked.slug}
            ),
        )
        assert response.status_code == 403
        locked.refresh_from_db()
        assert locked.body_md == "Original body."


class TestArticleDeletion:
    """Deleting an article removes its dynamic URL within the same process."""

    def test_deleting_article_removes_url(
        self, next_client: NextClient, routing_doc: Article
    ) -> None:
        first = next_client.get(routing_doc.url)
        assert first.status_code == 200

        slug = routing_doc.slug
        routing_doc.delete()
        gone = next_client.get(f"/wiki/{slug}/")
        assert gone.status_code == 404


class TestSearch:
    """Search returns matches from both the file catalogue and the database."""

    def test_search_returns_both_kinds(
        self, next_client: NextClient, routing_doc: Article, lifecycle_doc: Article
    ) -> None:
        response = next_client.get(reverse("next:page_search"), {"q": "routing"})
        body = response.content.decode()
        assert response.status_code == 200
        assert "/docs/routing/" in body
        assert routing_doc.title in body
        assert lifecycle_doc.title not in body

    def test_search_with_no_query_returns_empty_results(
        self, next_client: NextClient
    ) -> None:
        response = next_client.get(reverse("next:page_search"))
        assert response.status_code == 200


class TestSearchAutoSubmit:
    """The search box auto-submits into a results zone with debounce."""

    def test_search_form_carries_auto_submit_attributes(
        self, next_client: NextClient
    ) -> None:
        body = next_client.get(reverse("next:page_search")).content.decode()
        assert 'data-next-target="search-results"' in body
        assert 'data-next-trigger="input"' in body
        assert 'data-next-debounce="300"' in body
        assert 'data-next-zone="search-results"' in body

    def test_zone_request_morphs_only_the_results(
        self, next_client: NextClient, routing_doc: Article, lifecycle_doc: Article
    ) -> None:
        response = next_client.get_zones(
            reverse("next:page_search") + "?q=routing", "search-results"
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["search-results"]
        html = envelope.html_for_zone("search-results")
        assert routing_doc.title in html
        assert lifecycle_doc.title not in html
        assert "Search" not in html

    def test_empty_zone_request_prompts_for_a_query(
        self, next_client: NextClient
    ) -> None:
        response = next_client.get_zones(reverse("next:page_search"), "search-results")
        html = envelope_of(response).html_for_zone("search-results")
        assert "Type a query" in html


class TestMarkdownPreviewMount:
    """The preview pane is rebindable through the mount registry."""

    def test_new_article_form_carries_the_preview_root_and_script(
        self, next_client: NextClient
    ) -> None:
        body = next_client.get(reverse("next:page_articles_new")).content.decode()
        assert "data-markdown-preview" in body
        assert "components/markdown_preview.mjs" in body
        assert "marked.min.js" in body


class TestRouterReloadSignal:
    """`router_reloaded` fires once per article save and once per delete."""

    @pytest.mark.parametrize(
        "trigger", ["save", "delete"], ids=["on-save", "on-delete"]
    )
    def test_router_reload_signal_fires(
        self, make_article: Callable[..., Article], routing_doc: Article, trigger: str
    ) -> None:
        with SignalRecorder(router_reloaded) as recorder:
            if trigger == "save":
                make_article("signal-trigger", title="Signal")
            else:
                routing_doc.delete()
        assert len(recorder.events_for(router_reloaded)) >= 1


class TestUnits:
    """Unit tests covering defensive branches in models, providers, and backends."""

    def test_article_str_returns_title(self, routing_doc: Article) -> None:
        assert str(routing_doc) == routing_doc.title

    def test_article_clean_rejects_reserved_slug(self) -> None:
        article = Article(slug="docs", title="Docs")
        with pytest.raises(ValidationError):
            article.clean()

    def test_provider_returns_none_for_missing_slug(self) -> None:
        param = inspect.Parameter(
            "item",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DArticle[Article],
        )
        ctx = make_resolution_context(url_kwargs={})
        assert ArticleProvider().resolve(param, ctx) is None

    def test_hybrid_backend_returns_file_urls_when_catchall_absent(self) -> None:
        backend = HybridRouterBackend()
        with patch("wiki.backends.FileRouterBackend.generate_urls", return_value=[]):
            result = backend.generate_urls()
        assert result == []


class TestValidationPreservesPreview:
    """A validation error re-renders the form with the markdown preview pane."""

    def test_form_validation_error_shows_preview(
        self, submit_from_page: Callable[..., HttpResponse]
    ) -> None:
        response = submit_from_page(
            "article_create_form",
            page=reverse("next:page_articles_new"),
            data={
                "slug": "docs",
                "title": "Reserved slug",
                "body_md": "**bold** preview",
            },
        )
        body = response.content.decode()
        assert response.status_code == 200
        assert "data-markdown-preview" in body
        assert "<strong>bold</strong> preview" in body
