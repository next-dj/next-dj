from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from django.http import HttpRequest
from django.test import override_settings
from notes.backends import TenantPrefixStaticBackend
from notes.context_processors import tenant_theme
from notes.middleware import TenantMiddleware
from notes.models import Note, Tenant
from notes.providers import DTenant, TenantProvider


if TYPE_CHECKING:
    from types import ModuleType


EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
PAGES_ROOT = EXAMPLE_ROOT / "notes" / "workspaces"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_note_card = _load(
    PAGES_ROOT / "notes" / "_blocks" / "note_card" / "component.py",
    "mt_note_card",
)
_markdown_preview = _load(
    PAGES_ROOT / "notes" / "_blocks" / "markdown_preview" / "component.py",
    "mt_markdown_preview",
)


class TestTenantModelStr:
    """Model `__str__` surfaces friendly identifiers."""

    def test_tenant_str_carries_name_and_slug(self) -> None:
        tenant = Tenant(slug="acme", name="Acme Industries")
        rendered = str(tenant)
        assert "Acme Industries" in rendered
        assert "acme" in rendered

    @pytest.mark.django_db()
    def test_note_str_carries_tenant_and_title(self) -> None:
        tenant = Tenant.objects.create(slug="zeta", name="Zeta")
        note = Note.objects.create(tenant=tenant, title="hello")
        assert str(note) == "zeta/hello"


class TestTenantMiddleware:
    """`TenantMiddleware` enforces the X-Tenant contract with a DEBUG affordance."""

    def _request(self, **kwargs: object) -> HttpRequest:
        request = HttpRequest()
        request.method = "GET"
        request.path = kwargs.pop("path", "/notes/")
        request.META.update(kwargs.pop("meta", {}))
        request.GET = kwargs.pop("get", request.GET)  # type: ignore[assignment]
        request.COOKIES.update(kwargs.pop("cookies", {}))
        return request

    @override_settings(DEBUG=False)
    def test_missing_header_returns_400(self) -> None:
        middleware = TenantMiddleware(Mock())
        response = middleware(self._request())
        assert response.status_code == 400

    @pytest.mark.django_db()
    @override_settings(DEBUG=False)
    def test_unknown_tenant_returns_404(self) -> None:
        middleware = TenantMiddleware(Mock())
        response = middleware(
            self._request(meta={"HTTP_X_TENANT": "nope"}),
        )
        assert response.status_code == 404

    @pytest.mark.django_db()
    @override_settings(DEBUG=False)
    def test_header_attaches_tenant_and_calls_next(self) -> None:
        Tenant.objects.create(slug="payer", name="Payer")
        captured: dict[str, object] = {}

        def _next(request: HttpRequest) -> object:
            captured["tenant"] = request.tenant  # type: ignore[attr-defined]
            return Mock(status_code=200)

        middleware = TenantMiddleware(_next)
        middleware(self._request(meta={"HTTP_X_TENANT": "payer"}))
        assert isinstance(captured["tenant"], Tenant)
        assert captured["tenant"].slug == "payer"  # type: ignore[union-attr]

    @pytest.mark.django_db()
    @override_settings(DEBUG=False)
    def test_query_fallback_disabled_in_production(self) -> None:
        Tenant.objects.create(slug="payer", name="Payer")
        from django.http import QueryDict  # noqa: PLC0415

        request = self._request(get=QueryDict("tenant=payer"))
        middleware = TenantMiddleware(Mock())
        response = middleware(request)
        assert response.status_code == 400

    @pytest.mark.django_db()
    @override_settings(DEBUG=True)
    def test_debug_query_redirects_with_cookie(self) -> None:
        Tenant.objects.create(slug="payer", name="Payer")
        from django.http import QueryDict  # noqa: PLC0415

        request = self._request(get=QueryDict("tenant=payer"))
        middleware = TenantMiddleware(Mock())
        response = middleware(request)
        assert response.status_code == 302
        assert response.url == "/notes/"
        assert response.cookies["next_tenant"].value == "payer"

    @pytest.mark.django_db()
    @override_settings(DEBUG=True)
    def test_debug_cookie_used_when_header_missing(self) -> None:
        Tenant.objects.create(slug="payer", name="Payer")
        captured: dict[str, object] = {}

        def _next(request: HttpRequest) -> object:
            captured["tenant"] = request.tenant  # type: ignore[attr-defined]
            return Mock(status_code=200)

        middleware = TenantMiddleware(_next)
        middleware(self._request(cookies={"next_tenant": "payer"}))
        assert captured["tenant"].slug == "payer"  # type: ignore[union-attr]

    @pytest.mark.django_db()
    @override_settings(DEBUG=True)
    def test_unknown_cookie_clears_itself(self) -> None:
        """Stale `next_tenant` cookie is cleared so the user is not stuck on 404."""
        middleware = TenantMiddleware(Mock())
        response = middleware(self._request(cookies={"next_tenant": "ghost"}))
        assert response.status_code == 404
        cookie = response.cookies["next_tenant"]
        assert cookie.value == ""
        assert cookie["max-age"] == 0


class TestTenantProvider:
    """`TenantProvider` resolves DTenant only when a request carries a tenant."""

    def _context(self, request: object | None) -> Mock:
        return Mock(request=request)

    def _param(self, annotation: object) -> Mock:
        param = Mock()
        param.annotation = annotation
        return param

    def test_can_handle_matches_dtenant_with_request_tenant(self) -> None:
        provider = TenantProvider()
        request = HttpRequest()
        request.tenant = Tenant(slug="acme", name="Acme")  # type: ignore[attr-defined]
        assert provider.can_handle(self._param(DTenant), self._context(request))

    def test_can_handle_rejects_other_annotations(self) -> None:
        provider = TenantProvider()
        request = HttpRequest()
        request.tenant = Tenant(slug="acme", name="Acme")  # type: ignore[attr-defined]
        assert not provider.can_handle(self._param(int), self._context(request))

    def test_can_handle_rejects_missing_tenant(self) -> None:
        provider = TenantProvider()
        request = HttpRequest()
        assert not provider.can_handle(self._param(DTenant), self._context(request))

    def test_can_handle_rejects_missing_request(self) -> None:
        provider = TenantProvider()
        assert not provider.can_handle(self._param(DTenant), self._context(None))

    def test_resolve_returns_request_tenant(self) -> None:
        provider = TenantProvider()
        request = HttpRequest()
        tenant = Tenant(slug="acme", name="Acme")
        request.tenant = tenant  # type: ignore[attr-defined]
        assert provider.resolve(self._param(DTenant), self._context(request)) is tenant


class TestTenantTheme:
    """`tenant_theme` maps the active tenant to CSS variables."""

    def test_returns_empty_dict_when_request_has_no_tenant(self) -> None:
        request = HttpRequest()
        assert tenant_theme(request) == {"tenant_theme": {}, "tenant_theme_css": ""}

    def test_returns_css_variables_for_known_tenant(self) -> None:
        request = HttpRequest()
        request.tenant = Tenant(  # type: ignore[attr-defined]
            slug="acme",
            name="Acme",
            primary_color="#2563eb",
        )
        result = tenant_theme(request)
        assert result["tenant_theme"] == {"--tenant-accent": "#2563eb"}
        assert "#2563eb" in result["tenant_theme_css"]


class TestTenantPrefixStaticBackend:
    """The custom backend rewrites URLs only when a tenant is in scope."""

    def test_no_request_returns_url_unchanged(self) -> None:
        backend = TenantPrefixStaticBackend()
        assert backend.render_link_tag("/static/next/a.css", request=None) == (
            '<link rel="stylesheet" href="/static/next/a.css">'
        )

    def test_request_with_tenant_prepends_prefix(self) -> None:
        backend = TenantPrefixStaticBackend()
        request = HttpRequest()
        request.tenant = Tenant(slug="acme", name="Acme")  # type: ignore[attr-defined]
        rendered = backend.render_script_tag(
            "/static/next/a.js",
            request=request,
        )
        assert 'src="/_t/acme/static/next/a.js"' in rendered

    def test_absolute_external_url_passes_through(self) -> None:
        backend = TenantPrefixStaticBackend()
        request = HttpRequest()
        request.tenant = Tenant(slug="acme", name="Acme")  # type: ignore[attr-defined]
        rendered = backend.render_link_tag(
            "https://cdn.example.com/x.css",
            request=request,
        )
        assert "https://cdn.example.com/x.css" in rendered
        assert "/_t/" not in rendered


class TestNoteCardComponent:
    """`note_card` derives an excerpt and an edit URL for the listing."""

    def test_excerpt_truncates_long_body(self) -> None:
        body = "x" * 200
        assert _note_card.excerpt(Mock(body=body)).endswith("…")

    def test_excerpt_returns_short_body_unchanged(self) -> None:
        assert _note_card.excerpt(Mock(body="hello")) == "hello"


class TestMarkdownPreviewComponent:
    """`markdown_preview` renders markdown to HTML for the form preview pane."""

    def test_empty_body_returns_placeholder(self) -> None:
        rendered = _markdown_preview.render_html("")
        assert "Nothing to preview yet" in str(rendered)

    def test_body_renders_to_html(self) -> None:
        rendered = str(_markdown_preview.render_html("# Heading\n\nbody"))
        assert "<h1>Heading</h1>" in rendered
        assert "<p>body</p>" in rendered

    def test_inline_html_is_escaped(self) -> None:
        """Raw `<script>` survives only as escaped text, never as a live tag."""
        rendered = str(_markdown_preview.render_html("<script>alert(1)</script>"))
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered

    def test_javascript_href_is_neutralised(self) -> None:
        """Markdown auto-links to `javascript:` URLs lose their target."""
        rendered = str(
            _markdown_preview.render_html("[click](javascript:alert(1))"),
        )
        assert "javascript:" not in rendered
        assert 'href="#"' in rendered

    def test_safe_markdown_features_still_work(self) -> None:
        """Headings, fenced code, and ordinary links keep working."""
        rendered = str(
            _markdown_preview.render_html(
                "# Heading\n\n```py\nprint(1)\n```\n\n[ok](https://example.com)"
            ),
        )
        assert "<h1>Heading</h1>" in rendered
        assert "<pre>" in rendered
        assert 'href="https://example.com"' in rendered
