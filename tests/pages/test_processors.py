from unittest.mock import patch

import pytest
from django.http import HttpRequest
from django.template import Template
from django.test import override_settings

from next.conf import next_framework_settings
from next.pages.processors import (
    _get_context_processors,
    context_processors,
    page_context,
)
from tests.support import file_router_config_entry


def _router_with(*processor_paths: str) -> list[dict]:
    """Return one page-backend entry declaring the given processor paths."""
    return [
        file_router_config_entry(
            app_dirs=True, options={"context_processors": list(processor_paths)}
        )
    ]


def _templates_with(*processor_paths: str) -> list[dict]:
    """Return one Django template engine declaring the given processor paths."""
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"context_processors": list(processor_paths)},
        }
    ]


def _processor(request) -> dict[str, str]:
    """Stand-in context processor the import mock hands back."""
    return {"var": "value"}


def _other_processor(request) -> dict[str, str]:
    """Second stand-in, used where order matters."""
    return {"other": "value"}


@pytest.fixture()
def router_settings():
    """Configure one router processor and drop the memo around the test."""
    with override_settings(
        NEXT_FRAMEWORK={"PAGE_BACKENDS": _router_with("app.processors.one")},
        TEMPLATES=[],
    ):
        yield


class TestContextProcessorsMemo:
    """The merged processor list is built once per configuration."""

    def test_the_public_accessor_reads_the_memo(self, router_settings) -> None:
        with patch("next.pages.processors.import_callable", return_value=_processor):
            assert context_processors() is _get_context_processors()

    def test_a_series_of_calls_builds_the_list_once(self, router_settings) -> None:
        """The second call hands back the very list the first one built."""
        with patch(
            "next.pages.processors.import_callable", return_value=_processor
        ) as mock_import:
            first = _get_context_processors()
            second = _get_context_processors()

            assert first is second
            assert mock_import.call_count == 1

    def test_settings_reloaded_drops_the_memo(self, router_settings) -> None:
        """A framework-settings reload makes the next call rebuild."""
        with patch(
            "next.pages.processors.import_callable", return_value=_processor
        ) as mock_import:
            _get_context_processors()

            next_framework_settings.reload()

            assert _get_context_processors() == [_processor]
            assert mock_import.call_count == 2

    def test_a_templates_change_drops_the_memo(self) -> None:
        """The Django half of the merge moves without `settings_reloaded`."""
        with (
            override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": []}, TEMPLATES=[]),
            patch("next.pages.processors.import_callable", return_value=_processor),
        ):
            assert _get_context_processors() == []

            with override_settings(TEMPLATES=_templates_with("app.processors.one")):
                assert _get_context_processors() == [_processor]

    def test_the_memo_keeps_the_router_first_merge_order(self) -> None:
        """Router entries still precede the ``TEMPLATES`` ones across the memo."""
        with (
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": _router_with("app.processors.one")},
                TEMPLATES=_templates_with("app.processors.two"),
            ),
            patch("next.pages.processors.import_callable") as mock_import,
        ):
            mock_import.side_effect = [_processor, _other_processor]

            assert _get_context_processors() == [_processor, _other_processor]
            assert _get_context_processors() == [_processor, _other_processor]

    def test_the_memo_keeps_the_deduplication(self) -> None:
        """A path declared on both sides is still imported once."""
        shared = "app.processors.shared"
        with (
            override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": _router_with(shared)},
                TEMPLATES=_templates_with(shared),
            ),
            patch(
                "next.pages.processors.import_callable", return_value=_processor
            ) as mock_import,
        ):
            assert _get_context_processors() == [_processor]
            assert _get_context_processors() == [_processor]
            assert mock_import.call_count == 1

    def test_a_render_series_builds_the_list_once(
        self, page_instance, router_settings, tmp_path
    ) -> None:
        """Two context builds of one page share the memoised list."""
        page_file = tmp_path / "page.py"
        request = HttpRequest()
        with patch(
            "next.pages.processors.import_callable", return_value=_processor
        ) as mock_import:
            first = page_instance.build_render_context(page_file, request)
            second = page_instance.build_render_context(page_file, request)

            assert (first["var"], second["var"]) == ("value", "value")
            assert mock_import.call_count == 1


_STRINGS = {"BACKEND": "django.template.backends.dummy.TemplateStrings"}


class TestEngineProcessors:
    """The template processors come from the engine pages compile on."""

    def test_an_engine_ahead_of_django_templates_is_passed_over(self) -> None:
        templates = [_STRINGS, *_templates_with("app.processors.engine")]
        with (
            override_settings(TEMPLATES=templates),
            patch(
                "next.pages.processors._import_context_processor",
                return_value=_processor,
            ) as imported,
        ):
            _get_context_processors.cache_clear()
            context_processors()
            assert [call.args[0] for call in imported.call_args_list] == [
                "app.processors.engine"
            ]
        _get_context_processors.cache_clear()

    def test_no_django_templates_engine_lends_no_processors(self) -> None:
        with override_settings(TEMPLATES=[_STRINGS], NEXT_FRAMEWORK={}):
            _get_context_processors.cache_clear()
            assert context_processors() == []
        _get_context_processors.cache_clear()

    def test_a_malformed_list_lends_no_processors(self) -> None:
        templates = _templates_with()
        templates[0]["OPTIONS"]["context_processors"] = "app.processors.one"
        with override_settings(TEMPLATES=templates, NEXT_FRAMEWORK={}):
            _get_context_processors.cache_clear()
            assert context_processors() == []
        _get_context_processors.cache_clear()


@pytest.mark.urls("tests.support.instances")
class TestPageContext:
    """A page renders on a context carrying its request, as `{% url %}` reads it."""

    def test_url_reverses_into_the_current_app(self, rf) -> None:
        request = rf.get("/")
        request.current_app = "first"
        html = Template("{% url 'polls:index' %}").render(page_context({}, request))
        assert html == "/first/"

    def test_without_a_request_the_last_instance_answers(self) -> None:
        html = Template("{% url 'polls:index' %}").render(page_context({}, None))
        assert html == "/second/"

    def test_a_page_render_reverses_into_the_current_app(
        self, rf, page_instance, tmp_path
    ) -> None:
        request = rf.get("/")
        request.current_app = "first"
        html, _ = page_instance.render_with_static_assets(
            tmp_path / "page.py", "{% url 'polls:index' %}", {}, request=request
        )
        assert html == "/first/"

    def test_the_options_reach_the_context(self, rf) -> None:
        context = page_context({}, rf.get("/"), autoescape=False, use_tz=True)
        assert (context.autoescape, context.use_tz) == (False, True)
