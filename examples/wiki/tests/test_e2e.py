from __future__ import annotations

import re

import pytest
from django.urls import reverse
from wiki.models import Article

from next.testing import NextClient, SignalRecorder
from next.urls.signals import router_reloaded


pytestmark = pytest.mark.django_db


@pytest.fixture()
def routing_doc() -> Article:
    """Seed an article whose body matches the routing query for searches."""
    return Article.objects.create(
        slug="routing-internals",
        title="Routing internals",
        body_md="# Routing internals\n\nDeep dive on the URL pipeline.",
    )


@pytest.fixture()
def lifecycle_doc() -> Article:
    """Seed an unrelated article so listings have at least two rows."""
    return Article.objects.create(
        slug="lifecycle",
        title="Request lifecycle",
        body_md="Discusses every middleware stage.",
    )


class TestIndex:
    """The index page lists file-backed docs alongside DB-backed articles."""

    def test_index_lists_file_docs_and_articles(
        self, client: NextClient, routing_doc: Article, lifecycle_doc: Article
    ) -> None:
        response = client.get(reverse("next:page_"))
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
        self, client: NextClient, name: str, needle: str
    ) -> None:
        response = client.get(reverse(name))
        assert response.status_code == 200
        assert needle in response.content.decode()


class TestArticleCreation:
    """Posting the create form publishes a fresh `/wiki/<slug>/` URL."""

    def test_creating_article_publishes_url(self, client: NextClient) -> None:
        url = client.get_action_url("wiki:article_create")
        response = client.post(
            url,
            {
                "slug": "freshly-baked",
                "title": "Freshly baked",
                "body_md": "## Hello\n\nA brand new article.",
            },
        )
        assert response.status_code in (302, 303)
        assert Article.objects.filter(slug="freshly-baked").exists()

        article_response = client.get("/wiki/freshly-baked/")
        article_body = article_response.content.decode()
        assert article_response.status_code == 200
        assert "Freshly baked" in article_body
        assert "<h2>Hello</h2>" in article_body


class TestArticleEdit:
    """Saving the edit form replaces the persisted body."""

    def test_editing_article_changes_body(
        self, client: NextClient, routing_doc: Article
    ) -> None:
        url = client.get_action_url("wiki:article_edit")
        response = client.post(
            url,
            {
                "article_id": routing_doc.pk,
                "slug": routing_doc.slug,
                "title": routing_doc.title,
                "body_md": "Rewritten body of the article.",
            },
        )
        assert response.status_code in (302, 303)

        routing_doc.refresh_from_db()
        assert routing_doc.body_md == "Rewritten body of the article."

        article_response = client.get(routing_doc.url)
        assert "Rewritten body of the article." in article_response.content.decode()


class TestArticleDeletion:
    """Deleting an article removes its dynamic URL within the same process."""

    def test_deleting_article_removes_url(
        self, client: NextClient, routing_doc: Article
    ) -> None:
        first = client.get(routing_doc.url)
        assert first.status_code == 200

        slug = routing_doc.slug
        routing_doc.delete()
        gone = client.get(f"/wiki/{slug}/")
        assert gone.status_code == 404


class TestSearch:
    """Search returns matches from both the file catalogue and the database."""

    def test_search_returns_both_kinds(
        self, client: NextClient, routing_doc: Article, lifecycle_doc: Article
    ) -> None:
        response = client.get(reverse("next:page_search"), {"q": "routing"})
        body = response.content.decode()
        assert response.status_code == 200
        assert "/docs/routing/" in body
        assert routing_doc.title in body
        assert lifecycle_doc.title not in body


class TestRouterReloadSignal:
    """`router_reloaded` fires once per article save and once per delete."""

    def test_router_reload_signal_fires_on_article_save(self) -> None:
        with SignalRecorder(router_reloaded) as recorder:
            Article.objects.create(slug="signal-trigger", title="Signal", body_md="")
        assert len(recorder.events_for(router_reloaded)) >= 1

    def test_router_reload_signal_fires_on_article_delete(
        self, routing_doc: Article
    ) -> None:
        with SignalRecorder(router_reloaded) as recorder:
            routing_doc.delete()
        assert len(recorder.events_for(router_reloaded)) >= 1


class TestValidationPreservesPreview:
    """A validation error re-renders the form with the markdown preview pane."""

    def test_form_validation_error_shows_preview(self, client: NextClient) -> None:
        new_page = client.get(reverse("next:page_articles_new"))
        match = re.search(
            r'name="_next_form_page" value="([^"]+)"',
            new_page.content.decode(),
        )
        assert match, "Form did not render the hidden _next_form_page field."
        action_url = client.get_action_url("wiki:article_create")
        response = client.post(
            action_url,
            {
                "_next_form_page": match.group(1),
                "slug": "docs",
                "title": "Reserved slug",
                "body_md": "**bold** preview",
            },
        )
        body = response.content.decode()
        assert response.status_code == 200
        assert "data-markdown-preview" in body
        assert "<strong>bold</strong> preview" in body
