from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django import forms as django_forms
from django.forms import formset_factory
from django.template import Context

from next.forms import Form as NextForm
from next.forms.manager import form_action_manager
from next.forms.widgets import ComponentWidget, bind_component_widgets
from next.seeding import (
    COMPONENT_MODULE_PATH_KEY,
    PAGE_MODULE_PATH_KEY,
    TEMPLATE_PATH_KEY,
    RenderFrame,
)
from next.static import StaticCollector
from next.testing import override_component_backends
from tests.support.components import components_config
from tests.support.forms import echo_form, register_page_action


_OUTER_TEMPLATE = (
    '<div class="outer">{% load components %}{% component "inner" %}</div>'
)

_SERIALIZING_MODULE = (
    "from next import component\n"
    "\n"
    "\n"
    '@component.context("theme", serialize=True)\n'
    "def theme() -> str:\n"
    '    return "dark"\n'
)

_ACTION_TEMPLATE = '{% load forms %}<a href="{% action_url "widget_action" %}">go</a>'

_ANCHOR_TEMPLATE = "<i>{{ current_template_path }}</i>"


@pytest.fixture()
def nested_components(tmp_path: Path) -> Generator[Path, None, None]:
    """Register an `outer` component calling a composite `inner`, yield the anchor."""
    root = tmp_path / "_components"
    inner = root / "inner"
    inner.mkdir(parents=True)
    (inner / "component.djx").write_text("<span>inner</span>")
    (inner / "component.css").write_text(".inner {}")
    (root / "outer.djx").write_text(_OUTER_TEMPLATE)

    with override_component_backends(components_config(root)):
        yield tmp_path / "page.djx"


@pytest.fixture()
def serializing_component(tmp_path: Path) -> Generator[Path, None, None]:
    """Register a `themed` component publishing its context to the js side."""
    root = tmp_path / "_components"
    themed = root / "themed"
    themed.mkdir(parents=True)
    (themed / "component.djx").write_text("<div>theme={{ theme }}</div>")
    (themed / "component.py").write_text(_SERIALIZING_MODULE)

    with override_component_backends(components_config(root)):
        yield tmp_path / "page.djx"


@pytest.fixture()
def anchored_component(tmp_path: Path) -> Generator[Path, None, None]:
    """Register an `anchored` component echoing the anchor it rendered under."""
    root = tmp_path / "_components"
    root.mkdir()
    (root / "anchored.djx").write_text(_ANCHOR_TEMPLATE)

    with override_component_backends(components_config(root)):
        yield tmp_path / "page.djx"


@pytest.fixture()
def acting_component(tmp_path: Path) -> Generator[Path, None, None]:
    """Register a `linked` component whose body asks for a page-scoped action."""
    root = tmp_path / "_components"
    root.mkdir()
    (root / "linked.djx").write_text(_ACTION_TEMPLATE)

    with override_component_backends(components_config(root)):
        yield tmp_path / "page.djx"


class TestComponentWidgetComposition:
    """A component rendered as a widget carries the frame its body needs."""

    def test_nested_component_renders(self, nested_components: Path) -> None:
        widget = ComponentWidget("outer")
        widget._frame = RenderFrame(template_path=nested_components)

        html = widget.render("slug", "v", attrs={})

        assert "<span>inner</span>" in html

    def test_nested_component_assets_reach_the_collector(
        self, nested_components: Path
    ) -> None:
        collector = StaticCollector()
        widget = ComponentWidget("outer")
        widget._frame = RenderFrame(
            template_path=nested_components, collector=collector
        )

        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/components/inner.css",
        ):
            widget.render("slug", "v", attrs={})

        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        assert "/static/next/components/inner.css" in style_urls

    def test_an_unbound_widget_composes_from_the_fallback_anchor(
        self, nested_components: Path
    ) -> None:
        """The fallback anchor is seeded too, so a global root still composes."""
        widget = ComponentWidget("outer")

        html = widget.render("slug", "v", attrs={})

        assert "<span>inner</span>" in html

    def test_serialized_context_reaches_the_collector(
        self, serializing_component: Path
    ) -> None:
        collector = StaticCollector()
        widget = ComponentWidget("themed")
        widget._frame = RenderFrame(
            template_path=serializing_component, collector=collector
        )

        html = widget.render("slug", "v", attrs={})

        assert "theme=dark" in html
        assert collector.js_context()["theme"] == "dark"

    def test_page_scoped_action_url_resolves(
        self, acting_component: Path, tmp_path: Path
    ) -> None:
        page_module = tmp_path / "page.py"
        register_page_action(
            "widget_action", echo_form(ComponentWidget("echo")), str(page_module)
        )
        widget = ComponentWidget("linked")
        widget._frame = RenderFrame(
            template_path=acting_component, page_module_path=page_module
        )

        html = widget.render("slug", "v", attrs={})

        expected = form_action_manager.get_action_url(
            "widget_action", page_path=str(page_module)
        )
        assert f'href="{expected}"' in html


class TestBindComponentWidgetsPageAnchor:
    """The binder hands the page anchor down with the rest of the frame."""

    def test_sets_page_module_path(self, tmp_path: Path) -> None:
        page_module = tmp_path / "page.py"
        form = echo_form(ComponentWidget("echo"))()

        bind_component_widgets(
            form, template_path=tmp_path / "page.djx", page_module_path=page_module
        )

        assert form.fields["field"].widget._frame.page_module_path == page_module

    def test_page_module_path_defaults_to_none(self, tmp_path: Path) -> None:
        form = echo_form(ComponentWidget("echo"))()

        bind_component_widgets(form, template_path=tmp_path / "page.djx")

        assert form.fields["field"].widget._frame.page_module_path is None

    def test_formset_members_share_the_page_anchor(self, tmp_path: Path) -> None:
        page_module = tmp_path / "page.py"
        formset_class = formset_factory(echo_form(ComponentWidget("echo")), extra=2)
        formset = formset_class()

        bind_component_widgets(
            formset, template_path=tmp_path / "page.djx", page_module_path=page_module
        )

        for member in formset.forms:
            assert member.fields["field"].widget._frame.page_module_path == page_module


class TestFormTagAnchorReachesTheField:
    """The anchor a form resolved its action from is the one its fields inherit."""

    def test_a_component_scoped_action_reaches_the_field_component(
        self, acting_component: Path, tmp_path: Path, form_engine, csrf_request
    ) -> None:
        page_module = tmp_path / "page.py"
        component_module = tmp_path / "_components" / "owner" / "component.py"

        class _LinkedForm(NextForm):
            field = django_forms.CharField(widget=ComponentWidget("linked"))

        form_class = _LinkedForm
        register_page_action("widget_action", form_class, str(page_module))
        register_page_action("widget_action", form_class, str(component_module))
        page_url = form_action_manager.get_action_url(
            "widget_action", page_path=str(page_module)
        )
        component_url = form_action_manager.get_action_url(
            "widget_action", page_path=str(component_module)
        )
        assert page_url != component_url

        template = form_engine.from_string(
            '{% form "widget_action" %}{{ form.field }}{% endform %}'
        )
        html = template.render(
            Context(
                {
                    "request": csrf_request,
                    TEMPLATE_PATH_KEY: acting_component,
                    PAGE_MODULE_PATH_KEY: page_module,
                    COMPONENT_MODULE_PATH_KEY: component_module,
                }
            )
        )

        assert f'href="{component_url}"' in html
        assert f'href="{page_url}"' not in html

    def test_a_page_scoped_action_survives_a_component_anchored_form(
        self, acting_component: Path, tmp_path: Path, form_engine, csrf_request
    ) -> None:
        """The frame names the page beside the anchor the form resolved from."""
        page_module = tmp_path / "page.py"
        component_module = tmp_path / "_components" / "owner" / "component.py"

        class _LinkedForm(NextForm):
            field = django_forms.CharField(widget=ComponentWidget("linked"))

        register_page_action("owner_action", _LinkedForm, str(component_module))
        register_page_action("widget_action", _LinkedForm, str(page_module))
        page_url = form_action_manager.get_action_url(
            "widget_action", page_path=str(page_module)
        )

        template = form_engine.from_string(
            '{% form "owner_action" %}{{ form.field }}{% endform %}'
        )
        html = template.render(
            Context(
                {
                    "request": csrf_request,
                    TEMPLATE_PATH_KEY: acting_component,
                    PAGE_MODULE_PATH_KEY: page_module,
                    COMPONENT_MODULE_PATH_KEY: component_module,
                }
            )
        )

        assert f'href="{page_url}"' in html


class TestFormsetEmptyForm:
    """A formset builds `empty_form` on access, after the bind walked its members."""

    def test_the_empty_form_renders_under_the_frame_of_the_tag(
        self, anchored_component: Path, tmp_path: Path, form_engine, csrf_request
    ) -> None:
        page_module = tmp_path / "page.py"
        form_class = echo_form(ComponentWidget("anchored"))
        register_page_action("widget_action", form_class, str(page_module))
        formset_class = formset_factory(form_class, extra=1)

        template = form_engine.from_string(
            '{% form "widget_action" %}{{ form.empty_form.field }}{% endform %}'
        )
        html = template.render(
            Context(
                {
                    "request": csrf_request,
                    "widget_action": SimpleNamespace(form=formset_class()),
                    TEMPLATE_PATH_KEY: anchored_component,
                    PAGE_MODULE_PATH_KEY: page_module,
                }
            )
        )

        assert f"<i>{anchored_component}</i>" in html

    def test_a_widget_outside_a_form_keeps_the_fallback_anchor(
        self, anchored_component: Path
    ) -> None:
        """The ambient frame lives for the body of the tag and no longer."""
        widget = ComponentWidget("anchored")

        html = widget.render("slug", "v", attrs={})

        assert f"<i>{anchored_component}</i>" not in html
