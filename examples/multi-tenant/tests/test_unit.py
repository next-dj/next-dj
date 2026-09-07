import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.http import HttpRequest, QueryDict
from django.test import override_settings
from notes.backends import TenantPrefixStaticBackend
from notes.context_processors import tenant_theme
from notes.demo import DEMO_TENANTS, seed_demo
from notes.markdown_render import render_markdown
from notes.middleware import TenantMiddleware
from notes.models import Note, Tenant
from notes.providers import DTenant, TenantProvider
from notes.receivers import _on_form_access_denied


pytestmark = pytest.mark.django_db


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
    PAGES_ROOT / "notes" / "_blocks" / "note_card" / "component.py", "mt_note_card"
)


def _tenant_request(**overrides) -> HttpRequest:
    request = HttpRequest()
    tenant = Tenant(**{"slug": "acme", "name": "Acme", **overrides})
    request.tenant = tenant  # type: ignore[attr-defined]
    return request


def _capturing_next() -> tuple[dict[str, object], Callable[[HttpRequest], object]]:
    captured: dict[str, object] = {}

    def _next(request: HttpRequest) -> object:
        captured["tenant"] = request.tenant  # type: ignore[attr-defined]
        return Mock(status_code=200)

    return captured, _next


@pytest.fixture()
def tenant_request() -> Callable[..., HttpRequest]:
    return _tenant_request


@pytest.fixture()
def payer_tenant() -> Tenant:
    return Tenant.objects.create(slug="payer", name="Payer")


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

    def _request(self, **kwargs) -> HttpRequest:
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
        response = middleware(self._request(meta={"HTTP_X_TENANT": "nope"}))
        assert response.status_code == 404

    @pytest.mark.django_db()
    @override_settings(DEBUG=False)
    def test_header_attaches_tenant_and_calls_next(self, payer_tenant: Tenant) -> None:
        captured, _next = _capturing_next()
        middleware = TenantMiddleware(_next)
        middleware(self._request(meta={"HTTP_X_TENANT": payer_tenant.slug}))
        assert isinstance(captured["tenant"], Tenant)
        assert captured["tenant"] == payer_tenant

    @pytest.mark.django_db()
    @override_settings(DEBUG=False)
    def test_query_fallback_disabled_in_production(self, payer_tenant: Tenant) -> None:
        request = self._request(get=QueryDict(f"tenant={payer_tenant.slug}"))
        middleware = TenantMiddleware(Mock())
        response = middleware(request)
        assert response.status_code == 400

    @pytest.mark.django_db()
    @override_settings(DEBUG=True)
    def test_debug_query_redirects_with_cookie(self, payer_tenant: Tenant) -> None:
        request = self._request(get=QueryDict(f"tenant={payer_tenant.slug}"))
        middleware = TenantMiddleware(Mock())
        response = middleware(request)
        assert response.status_code == 302
        assert response.url == "/notes/"
        assert response.cookies["next_tenant"].value == payer_tenant.slug

    @pytest.mark.django_db()
    @override_settings(DEBUG=True)
    def test_debug_cookie_used_when_header_missing(self, payer_tenant: Tenant) -> None:
        captured, _next = _capturing_next()
        middleware = TenantMiddleware(_next)
        middleware(self._request(cookies={"next_tenant": payer_tenant.slug}))
        assert captured["tenant"] == payer_tenant

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

    @pytest.mark.parametrize(
        ("annotation", "http_request", "expected"),
        [
            (DTenant, _tenant_request(), True),
            (int, _tenant_request(), False),
            (DTenant, HttpRequest(), False),
            (DTenant, None, False),
        ],
        ids=[
            "dtenant_with_request_tenant",
            "other_annotation",
            "request_without_tenant",
            "no_request",
        ],
    )
    def test_can_handle(self, annotation, http_request, expected) -> None:
        provider = TenantProvider()
        context = self._context(http_request)
        assert provider.can_handle(self._param(annotation), context) is expected

    def test_resolve_returns_request_tenant(
        self, tenant_request: Callable[..., HttpRequest]
    ) -> None:
        provider = TenantProvider()
        request = tenant_request()
        resolved = provider.resolve(self._param(DTenant), self._context(request))
        assert resolved is request.tenant


class TestTenantTheme:
    """`tenant_theme` maps the active tenant to CSS variables."""

    def test_returns_empty_dict_when_request_has_no_tenant(self) -> None:
        request = HttpRequest()
        assert tenant_theme(request) == {"tenant_theme": {}, "tenant_theme_css": ""}

    def test_returns_css_variables_for_known_tenant(
        self, tenant_request: Callable[..., HttpRequest]
    ) -> None:
        request = tenant_request(primary_color="#2563eb")
        result = tenant_theme(request)
        assert result["tenant_theme"] == {"--tenant-accent": "#2563eb"}
        assert "#2563eb" in result["tenant_theme_css"]


class TestTenantPrefixStaticBackend:
    """The custom backend rewrites URLs only when a tenant is in scope."""

    def test_no_request_returns_url_unchanged(self) -> None:
        backend = TenantPrefixStaticBackend()
        assert backend.asset_url("/static/next/a.css", request=None) == (
            "/static/next/a.css"
        )

    def test_request_without_tenant_returns_url_unchanged(self) -> None:
        backend = TenantPrefixStaticBackend()
        assert backend.asset_url("/static/next/a.css", request=HttpRequest()) == (
            "/static/next/a.css"
        )

    def test_request_with_tenant_prepends_prefix(
        self, tenant_request: Callable[..., HttpRequest]
    ) -> None:
        backend = TenantPrefixStaticBackend()
        url = backend.asset_url("/static/next/a.js", request=tenant_request())
        assert url == "/_t/acme/static/next/a.js"

    def test_absolute_external_url_passes_through(
        self, tenant_request: Callable[..., HttpRequest]
    ) -> None:
        backend = TenantPrefixStaticBackend()
        url = backend.asset_url(
            "https://cdn.example.com/x.css", request=tenant_request()
        )
        assert url == "https://cdn.example.com/x.css"

    def test_rendered_tags_still_use_the_default_markup(
        self, tenant_request: Callable[..., HttpRequest]
    ) -> None:
        """Markup stays the default, the prefix is applied by the pipeline."""
        backend = TenantPrefixStaticBackend()
        request = tenant_request()
        assert backend.render_link_tag("/x.css", request=request) == (
            '<link rel="stylesheet" href="/x.css">'
        )


class TestNoteCardComponent:
    """`note_card` derives an excerpt and an edit URL for the listing."""

    def test_excerpt_truncates_long_body(self) -> None:
        body = "x" * 200
        assert _note_card.excerpt(Mock(body=body)).endswith("…")

    def test_excerpt_returns_short_body_unchanged(self) -> None:
        assert _note_card.excerpt(Mock(body="hello")) == "hello"


class TestFormAccessDeniedReceiver:
    """`_on_form_access_denied` logs the denied action, layer, reason, and tenant."""

    def test_receiver_logs_tenant_slug_from_request(
        self,
        caplog: pytest.LogCaptureFixture,
        tenant_request: Callable[..., HttpRequest],
    ) -> None:
        request = tenant_request()
        with caplog.at_level(logging.WARNING, logger="notes.access"):
            _on_form_access_denied(
                action_name="note_edit_form",
                layer="object",
                reason="raised",
                request=request,
            )
        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        message = record.getMessage()
        assert "action=note_edit_form" in message
        assert "layer=object" in message
        assert "reason=raised" in message
        assert "tenant=acme" in message

    def test_receiver_falls_back_when_request_has_no_tenant(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="notes.access"):
            _on_form_access_denied(
                action_name="note_edit_form",
                layer="view",
                reason="denied",
                request=HttpRequest(),
            )
        assert "tenant=unknown" in caplog.records[0].getMessage()


class TestMarkdownRender:
    """`render_markdown` produces safe preview HTML for the note form pane."""

    def test_empty_body_returns_placeholder(self) -> None:
        rendered = render_markdown("")
        assert "Nothing to preview yet" in str(rendered)

    def test_body_renders_to_html(self) -> None:
        rendered = str(render_markdown("# Heading\n\nbody"))
        assert "<h1>Heading</h1>" in rendered
        assert "<p>body</p>" in rendered

    def test_inline_html_is_escaped(self) -> None:
        """Raw `<script>` survives only as escaped text, never as a live tag."""
        rendered = str(render_markdown("<script>alert(1)</script>"))
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered

    def test_javascript_href_is_neutralised(self) -> None:
        """Markdown auto-links to `javascript:` URLs lose their target."""
        rendered = str(render_markdown("[click](javascript:alert(1))"))
        assert "javascript:" not in rendered
        assert 'href="#"' in rendered

    def test_safe_markdown_features_still_work(self) -> None:
        """Headings, fenced code, and ordinary links keep working."""
        rendered = str(
            render_markdown(
                "# Heading\n\n```py\nprint(1)\n```\n\n[ok](https://example.com)"
            )
        )
        assert "<h1>Heading</h1>" in rendered
        assert "<pre>" in rendered
        assert 'href="https://example.com"' in rendered


class TestDemoSeed:
    """The demo dataset lives in a seed module rather than a data migration."""

    def test_command_creates_tenants_notes_and_the_lock(self) -> None:
        call_command("seed_demo")
        assert set(Tenant.objects.values_list("slug", flat=True)) == {"acme", "globex"}
        assert Note.objects.count() == 3
        locked = Note.objects.get(tenant__slug="acme", title="Status update")
        assert locked.locked is True

    def test_seeding_twice_keeps_one_copy(self, demo_data) -> None:
        seed_demo()
        assert Tenant.objects.count() == len(DEMO_TENANTS)
        assert Note.objects.count() == 3
