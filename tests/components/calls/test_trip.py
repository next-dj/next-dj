from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, TemplateSyntaxError

from next.components import ComponentContextConflictError
from next.components.trip import TripInput, prop_types
from next.defer import DeferMode
from next.suspense.components import deferring
from next.suspense.tokens import decode_props
from next.suspense.tokens.props import DEFAULT_LIMIT
from next.suspense.trip import claims_for
from next.templatetags import components as component_tags
from next.testing import boundary_errors
from next.testing.boundaries import recorded_failures
from tests.components.sites import HEAD, PROPS, TRIP, ComponentSite, Member


class TestClientTrip:
    """A client-trip call leaves a token wrapper and never runs its component."""

    def test_props_that_fail_to_resolve_render_in_place(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", error="E", loading="L", module=TRIP)
        with deferring(request=request_for(AnonymousUser())):
            shell = site.render("{% component 'card' x=boom %}")
        assert "data-next-token" not in shell
        assert "E" in shell
        assert "<b>" not in shell

    def test_children_are_refused(self, site: ComponentSite) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        with pytest.raises(TemplateSyntaxError, match=r"next\.E224"):
            site.render("{% #component 'card' x=1 %}kid{% /component %}")

    def test_without_a_sink_the_call_renders_in_place(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        slot = SimpleNamespace(peek=lambda: None)
        with patch("next.components.boundaries.deferrals_slot", slot):
            html = site.render("{% component 'card' x=1 %}", request=request_for(None))
        assert "<b>1</b>" in html
        assert "data-next-token" not in html

    def test_the_request_of_the_deferring_block_mints(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        with deferring(request=request_for(AnonymousUser())) as held:
            shell = site.render("{% component 'card' x='1' %}")
        assert 'data-next-defer="load"' in shell
        assert "data-next-token=" in shell
        assert held.session is not None
        assert not held.session.jobs

    @pytest.mark.parametrize("value", [False, None, 0, 1.5, ["a", 2]])
    def test_an_unannotated_prop_reloads_with_its_inline_value(
        self, site: ComponentSite, value: object, request_for
    ) -> None:
        info = site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        request = request_for(AnonymousUser())
        trip = TripInput(info, DeferMode.LOAD, {"x": value})
        claims = claims_for(request, trip, DEFAULT_LIMIT)
        decoded = decode_props(request, claims.props, prop_types(info))
        assert decoded == {"x": value}
        assert type(decoded["x"]) is type(value)

    def test_a_literal_false_prop_mints_a_token(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        with deferring(request=request_for(AnonymousUser())):
            shell = site.render("{% component 'card' x=False %}")
        assert "data-next-token=" in shell

    def test_no_request_streams_the_call_instead(self, site: ComponentSite) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        with deferring() as held:
            shell = site.render("{% component 'card' x=1 %}")
        assert "data-next-token" not in shell
        assert held.session is not None
        assert held.session.jobs[0].site.mode is DeferMode.STREAM

    def test_a_call_inside_a_stored_render_stays_inline(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        html = site.render(
            "{% load cache %}{% cache 60 trip %}{% component 'card' x=1 %}{% endcache %}"
        )
        assert "<b>1</b>" in html
        assert "data-next-token" not in html


class TestPlainRetry:
    """A plain guarded call that declares props retries with a token of its own."""

    def _failed(
        self, site: ComponentSite, request_for, module: str, user: object
    ) -> str:
        site.add("card", "<b>{{ x }}{{ boom }}</b>", error="E", module=module)
        return site.render("{% component 'card' x='1' %}", request=request_for(user))

    def test_a_failed_call_carries_the_token_alone(
        self, site: ComponentSite, request_for
    ) -> None:
        html = self._failed(site, request_for, PROPS, AnonymousUser())
        assert 'data-next-state="error"' in html
        assert "data-next-token=" in html
        assert "data-next-boundary-exp" not in html

    def test_a_call_whose_rule_passed_carries_the_token(
        self, site: ComponentSite, request_for
    ) -> None:
        html = self._failed(
            site, request_for, PROPS + "component.login_required()\n", Member()
        )
        assert "data-next-token=" in html

    @pytest.mark.parametrize(
        ("module", "user"),
        [
            pytest.param(HEAD, AnonymousUser(), id="no-props"),
            pytest.param(
                PROPS + "component.login_required()\n", AnonymousUser(), id="refused"
            ),
        ],
    )
    def test_no_token_without_props_or_a_passed_rule(
        self, site: ComponentSite, module: str, user: object, request_for
    ) -> None:
        assert "data-next-token" not in self._failed(site, request_for, module, user)

    def test_a_component_naming_no_props_retries_on_its_own(
        self, site: ComponentSite, request_for
    ) -> None:
        html = self._failed(
            site, request_for, HEAD + "component.props()\n", AnonymousUser()
        )
        assert "data-next-token=" in html

    def test_a_stream_component_naming_no_props_retries_on_its_own(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add(
            "card",
            "{{ boom }}",
            error="E",
            loading="L",
            module=HEAD + "component.props()\n",
        )
        html = site.finished("{% component 'card' %}", request=request_for(None))
        assert 'data-next-state="error"' in html
        assert "data-next-token=" in html

    def test_no_token_without_a_sink_or_a_request(
        self, site: ComponentSite, request_for
    ) -> None:
        slot = SimpleNamespace(peek=lambda: None)
        with patch("next.components.boundaries.deferrals_slot", slot):
            html = self._failed(site, request_for, PROPS, AnonymousUser())
        assert "data-next-token" not in html
        site.add("bare", "<b>{{ x }}{{ boom }}</b>", error="E", module=PROPS)
        assert "data-next-token" not in site.render("{% component 'bare' x=1 %}")


DEFAULTS = (
    HEAD
    + 'component.props("x")\n'
    + '@component.context\ndef defaults():\n    return {"x": "context"}\n'
)


class TestKeylessContextAgainstProps:
    """A reload meets a keyless context the way the inline render of its call does."""

    def _reloaded(self, site: ComponentSite, props: dict[str, object]) -> str:
        info = site.infos["card"]
        trip = TripInput(info, DeferMode.LOAD, props)
        renderer = component_tags.isolated_renderer(info, site.root, trip)
        return renderer.body(Context(site.context()))[0]

    def test_a_passed_prop_conflicts_on_both_paths(self, site: ComponentSite) -> None:
        site.add("card", "<b>{{ x }}</b>", error="E", module=DEFAULTS)
        with boundary_errors("render"), recorded_failures() as failures:
            inline = site.render("{% component 'card' x=1 %}")
            reloaded = self._reloaded(site, {"x": 1})
        assert "E" in inline
        assert reloaded == "E"
        assert [type(failure.exception) for failure in failures] == [
            ComponentContextConflictError,
            ComponentContextConflictError,
        ]

    def test_an_unpassed_prop_takes_the_context_on_both_paths(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", module=DEFAULTS)
        assert site.render("{% component 'card' %}") == "<b>context</b>"
        assert self._reloaded(site, {}) == "<b>context</b>"
