from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.template import Context, Engine, Template, TemplateSyntaxError
from django.test import Client

from next.boundaries.calls import DEFERRALS_KEY, DeferredCall
from next.boundaries.options import DeferMode
from next.components import tags as component_tags
from next.components.tags import ComponentNode
from next.components.trip import TripInput
from next.defer.assembly import finish
from next.defer.components import deferring
from next.seeding import COLLECTOR_KEY
from tests.components.sites import (
    BOOM,
    LOGIN,
    OBJECT,
    PROPS,
    TRIP,
    Admin,
    ComponentSite,
    Member,
    template_engine,
)
from tests.support import routed
from tests.support.access import COMPONENTS
from tests.support.boundaries import run_sequentially
from tests.support.pages import write_page


class TestAccess:
    """A rule without a context object runs in the shell, one with it in the job."""

    @pytest.mark.parametrize(
        ("user", "shown"), [(AnonymousUser(), False), (Member(), True)]
    )
    def test_a_shell_rule_refuses_before_any_wrapper(
        self, site: ComponentSite, user: object, request_for, *, shown: bool
    ) -> None:
        site.add("card", "<b>C</b>", loading="L{{ t }}", module=LOGIN)
        with deferring() as held:
            shell = site.render(
                "[{% component 'card' t='T' %}]", request=request_for(user)
            )
        if shown:
            assert "LT</div>" in shell
            assert held.session is not None
        else:
            assert shell == "[]"
            assert held.session is None

    def test_a_rule_reading_the_context_refuses_a_signed_out_visitor_in_the_shell(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ t }}</b>", loading="L", module=OBJECT)
        with deferring() as held:
            shell = site.render(
                "[{% component 'card' t='T' %}]", request=request_for(AnonymousUser())
            )
        assert shell == "[]"
        assert held.session is None

    @pytest.mark.parametrize(("allowed", "body"), [("acme", "<b>T</b>"), ("x", "")])
    def test_a_rule_reading_the_context_runs_in_the_job(
        self, site: ComponentSite, allowed: str, body: str, request_for
    ) -> None:
        site.add("card", "<b>{{ t }}</b>", loading="L[{{ t }}]", module=OBJECT)
        shell, session = site.deferred(
            "{% component 'card' t='T' %}", request=request_for(Member(allowed))
        )
        assert "L[]</div>" in shell
        html = finish(shell, session, site.collector, run_sequentially)
        assert html.endswith(f'data-next-state="ready">{body}</div>')


class TestDeniedAssets:
    """A component its access rule denies adds no asset, deferred or inline."""

    def test_a_job_denied_by_its_rule_adds_nothing(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ t }}</b>", loading="L", module=OBJECT)
        site.finished("{% component 'card' t='T' %}", request=request_for(Member("x")))
        assert site.styles() == []

    @pytest.mark.parametrize(
        ("user", "html", "styles"),
        [(AnonymousUser(), "", []), (Member(), "<b>C</b>", ["/card.css"])],
    )
    def test_an_inline_rule_keeps_the_assets_of_a_shown_component_alone(
        self,
        site: ComponentSite,
        user: object,
        html: str,
        styles: list[str],
        request_for,
    ) -> None:
        site.add("card", "<b>C</b>", module=LOGIN)
        assert site.render("{% component 'card' %}", request=request_for(user)) == html
        assert site.styles() == styles
        assert site.collector._journal is None

    def test_a_ruled_component_that_raises_takes_its_assets_back(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "{{ boom }}", module=LOGIN)
        with pytest.raises(ValueError, match=BOOM):
            site.render("{% component 'card' %}", request=request_for(Member()))
        assert site.styles() == []
        assert site.collector._journal is None

    def test_without_a_collector_the_render_runs_plainly(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>C</b>", module=LOGIN)
        html = site.render(
            "{% component 'card' %}",
            request=request_for(Member()),
            **{COLLECTOR_KEY: None},
        )
        assert html == "<b>C</b>"


class _Recorder:
    """A sink that keeps the token input of every client-trip call it is handed."""

    def __init__(self) -> None:
        self.trips: list[TripInput | None] = []

    def queue(self, context: Context, call: DeferredCall) -> str:
        self.trips.append(call.renderer.trip_input())
        return ""


class TestEnclosingPermissions:
    """A call records the literal permissions of the granting branches around it."""

    @pytest.mark.parametrize(
        ("source", "perms"),
        [
            pytest.param("{% component 'card' %}", (), id="unguarded"),
            pytest.param(
                "{% has_perm 'a.one' %}{% has_perm 'a.two' %}{% has_perm 'a.one' %}"
                "{% component 'card' %}{% endhas_perm %}{% endhas_perm %}"
                "{% endhas_perm %}",
                ("a.one", "a.two"),
                id="literals",
            ),
            pytest.param(
                "{% has_perm 'a.one' %}{% else %}{% component 'card' %}{% endhas_perm %}",
                (),
                id="denying-branch",
            ),
            pytest.param(
                "{% has_perm 'a.one' %}{% endhas_perm %}{% component 'card' %}",
                (),
                id="closed-guard",
            ),
            pytest.param(
                "{% has_perm name %}{% component 'card' %}{% endhas_perm %}",
                None,
                id="variable",
            ),
            pytest.param(
                "{% has_perm 'a.one' %}{% has_perm 'a.two' row %}{% component 'card' %}"
                "{% endhas_perm %}{% endhas_perm %}",
                None,
                id="object",
            ),
        ],
    )
    def test_the_guards_are_read_as_the_call_compiles(
        self, source: str, perms: tuple[str, ...] | None
    ) -> None:
        (node,) = Template(source).nodelist.get_nodes_by_type(ComponentNode)
        assert node.perms == perms

    def test_a_guard_around_a_nested_block_of_a_child_template_is_claimed(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        engine = template_engine(
            {
                "base.html": "{% block a %}{% endblock %}",
                "page.html": "{% extends 'base.html' %}{% block a %}"
                "{% has_perm 'shop.view_cart' %}<ul>{% block b %}"
                "{% component 'card' x='1' %}{% endblock %}</ul>"
                "{% endhas_perm %}{% endblock %}",
            },
            permissions=True,
        )
        sink = _Recorder()
        context = site.context(request=request_for(Admin()), **{DEFERRALS_KEY: sink})
        engine.get_template("page.html").render(Context(context))
        (trip,) = sink.trips
        assert trip is not None
        assert trip.perms == ("shop.view_cart",)

    def test_a_client_trip_under_an_unnamed_guard_is_refused(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        with pytest.raises(TemplateSyntaxError, match="literal permission"):
            site.render(
                "{% has_perm name %}{% component 'card' x='1' %}{% endhas_perm %}",
                name="a.one",
                request=request_for(Admin()),
                **{DEFERRALS_KEY: _Recorder()},
            )

    @pytest.mark.parametrize(
        ("guard", "minted"), [("'a.one'", True), ("name", False)], ids=str
    )
    def test_a_retry_under_an_unnamed_guard_mints_no_token(
        self, site: ComponentSite, guard: str, request_for, *, minted: bool
    ) -> None:
        site.add("card", "<b>{{ x }}{{ boom }}</b>", error="E", module=PROPS)
        html = site.render(
            f"{{% has_perm {guard} %}}{{% component 'card' x='1' %}}{{% endhas_perm %}}",
            name="a.one",
            request=request_for(Admin()),
        )
        assert 'data-next-state="error"' in html
        assert ("data-next-token=" in html) is minted


_GUARDED_TEMPLATES = {
    "base.html": "{% has_perm 'x.y' %}{% block body %}{% endblock %}{% endhas_perm %}",
    "child.html": (
        "{% extends 'base.html' %}{% block body %}{% component 'card' x='1' %}"
        "{% endblock %}"
    ),
    "scoped.html": (
        "{% has_perm 'x.y' row %}{% block body %}{% endblock %}{% endhas_perm %}"
    ),
    "scoped_child.html": (
        "{% extends 'scoped.html' %}{% block body %}{% component 'card' x='1' %}"
        "{% endblock %}"
    ),
    "part.html": "{% component 'card' x='1' %}",
    "including.html": (
        "{% has_perm 'x.y' %}{% include 'part.html' %}{% endhas_perm %}"
    ),
    "named.html": "{% has_perm name %}{% include 'part.html' %}{% endhas_perm %}",
}


class TestRenderedGuards:
    """A call claims the granting branches other templates render around it."""

    @pytest.fixture()
    def engine(self) -> Engine:
        return template_engine(_GUARDED_TEMPLATES, permissions=True)

    def _trips(
        self, site: ComponentSite, request_for, engine: Engine, name: str
    ) -> list[TripInput | None]:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        sink = _Recorder()
        context = site.context(
            request=request_for(Admin()), name="x.y", row=1, **{DEFERRALS_KEY: sink}
        )
        engine.get_template(name).render(Context(context))
        return sink.trips

    @pytest.mark.parametrize("name", ["child.html", "including.html"])
    def test_a_guard_of_another_template_is_claimed(
        self, site: ComponentSite, engine: Engine, name: str, request_for
    ) -> None:
        (trip,) = self._trips(site, request_for, engine, name)
        assert trip is not None
        assert trip.perms == ("x.y",)

    @pytest.mark.parametrize("name", ["scoped_child.html", "named.html"])
    def test_an_unnamed_guard_of_another_template_refuses_a_client_trip(
        self, site: ComponentSite, engine: Engine, name: str, request_for
    ) -> None:
        with pytest.raises(TemplateSyntaxError, match="literal permission"):
            self._trips(site, request_for, engine, name)

    def test_a_guard_around_a_component_reaches_the_call_in_its_template(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        site.add("outer", "<o>{% component 'card' x='1' %}</o>")
        sink = _Recorder()
        site.render(
            "{% has_perm 'x.y' %}{% component 'outer' %}{% endhas_perm %}",
            request=request_for(Admin()),
            **{DEFERRALS_KEY: sink},
        )
        (trip,) = sink.trips
        assert trip is not None
        assert trip.perms == ("x.y",)

    def test_a_job_renders_under_the_guards_open_when_it_was_queued(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        site.add("outer", "<o>{% component 'card' x='1' %}</o>", loading="O")
        with patch.object(component_tags, "TripInput", wraps=TripInput) as minted:
            site.finished(
                "{% has_perm 'x.y' %}{% component 'outer' %}{% endhas_perm %}",
                request=request_for(Admin()),
            )
        assert [call.args[3] for call in minted.call_args_list] == [("x.y",)]

    def test_an_isolated_render_keeps_the_claimed_guards(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=TRIP)
        info = site.add("outer", "<o>{% component 'card' x='1' %}</o>")
        sink = _Recorder()
        trip = TripInput(info, DeferMode.LOAD, {}, ("x.y",))
        renderer = component_tags.isolated_renderer(info, site.root, trip)
        renderer.body(
            Context(site.context(request=request_for(Admin()), **{DEFERRALS_KEY: sink}))
        )
        (inner,) = sink.trips
        assert inner is not None
        assert inner.perms == ("x.y",)


@pytest.fixture()
def guarded_layout(tmp_path: Path) -> Iterator[Path]:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "layout.djx").write_text(
        "<html>{% has_perm 'x.y' %}{% template %}{% endhas_perm %}</html>"
    )
    card = pages / "_components" / "card"
    card.mkdir(parents=True)
    (card / "component.djx").write_text("<b>{{ x }}</b>")
    (card / "loading.djx").write_text("<b>L</b>")
    (card / "component.py").write_text(TRIP)
    write_page(pages, "home", "", body="{% component 'card' x='1' %}")
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        yield pages


@pytest.mark.django_db()
@pytest.mark.usefixtures("guarded_layout")
def test_a_guard_of_the_layout_around_the_page_slot_is_claimed() -> None:
    client = Client()
    client.force_login(User.objects.create_superuser("root", password="pw"))
    with patch.object(component_tags, "TripInput", wraps=TripInput) as minted:
        response = client.get("/home/")
    assert response.status_code == 200
    assert "data-next-token=" in response.content.decode()
    assert [call.args[3] for call in minted.call_args_list] == [("x.y",)]
