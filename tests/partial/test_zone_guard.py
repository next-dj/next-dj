from dataclasses import dataclass, field
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import override_settings

from next.forms.origin import OriginMatch
from next.pages import page
from next.pages.errors import PageModuleImportError
from next.partial import (
    ForeignPageNotAuthorizedError,
    Patches,
    UnknownZoneError,
    patches as patches_module,
)
from next.partial.registry import zones_of
from next.partial.render import render_zone
from next.partial.shaping.targets import _declared_zones, _form_zone
from next.partial.signals import zone_registered
from next.testing import NextClient, capture_signals, envelope_of
from tests.pages.checks.metadata.trees import (
    COMPONENTS,
    component_template,
    layout_root,
    templated_page,
)
from tests.support import (
    PermissionHolder,
    build_zone_request,
    partial_request,
    routed,
    touch_later,
)
from tests.support.access import (
    PAGE_HEAD,
    client_as,
    login_required_middleware,
    login_required_setting,
    write_guard,
)
from tests.support.guards import (
    CHILDREN_BODY,
    GUARDED_CHILDREN,
    GUARDED_SET_SLOT,
    GUARDED_SLOT_VARIABLE,
    NESTED_COMPONENT_BODY,
    PERM,
    PLAIN_COMPONENT,
    fill_slot,
    wrap,
)


_GUARD = f'{{% has_perm "{PERM}" %}}'
_END = "{% endhas_perm %}"
_PREFIX = "PREFIX"

_GUARDED = (
    '<p>public</p>{% zone "pub" %}<i>open</i>{% endzone %}'
    f'{_GUARD}{{% zone "secret" %}}SECRET{{% endzone %}}{_END}'
)


@dataclass(frozen=True, slots=True)
class _Leak:
    """A page whose zone renders only behind a check, and the zone to ask for."""

    id: str
    body: str
    zone: str = "cz"
    components: dict[str, str] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)
    layout: bool = False
    holder_only: bool = True


_LEAKS = (
    _Leak(
        "direct",
        f'<p>public</p>{_GUARD}{{% zone "secret" %}}SECRET{{% endzone %}}{_END}',
        "secret",
    ),
    _Leak(
        "nested",
        f'{{% zone "outer" %}}<b>outer</b>{_GUARD}{{% zone "inner" %}}SECRET'
        f"{{% endzone %}}{_END}{{% endzone %}}",
        "inner",
    ),
    _Leak("layout", '{% zone "lz" %}SECRET{% endzone %}', "lz", layout=True),
    _Leak("set-slot", fill_slot("content"), components={"guarded": GUARDED_SET_SLOT}),
    _Leak(
        "slot-variable",
        fill_slot("content"),
        components={"guarded": GUARDED_SLOT_VARIABLE},
    ),
    _Leak("children", CHILDREN_BODY, components={"guarded": GUARDED_CHILDREN}),
    _Leak(
        "unguarded-slot-of-a-guard",
        fill_slot("aside"),
        components={"guarded": GUARDED_SET_SLOT},
        holder_only=False,
    ),
    _Leak(
        "nested-component",
        NESTED_COMPONENT_BODY,
        components={"guarded": GUARDED_SET_SLOT, "plain": PLAIN_COMPONENT},
    ),
    _Leak(
        "forwarded-into-a-guard",
        wrap("forward"),
        components={
            "forward": "<section>" + wrap("guarded", "{{ children }}") + "</section>",
            "guarded": GUARDED_CHILDREN,
        },
    ),
    _Leak(
        "with-alias",
        CHILDREN_BODY,
        components={
            "guarded": f"{{% with c=children %}}{_GUARD}{{{{ c }}}}{_END}{{% endwith %}}"
        },
    ),
    _Leak(
        "include",
        CHILDREN_BODY,
        components={"guarded": f'<div>{{% include "{_PREFIX}_part.html" %}}</div>'},
        templates={"part.html": f"{_GUARD}{{{{ children }}}}{_END}"},
    ),
    _Leak(
        "extends",
        CHILDREN_BODY,
        components={
            "guarded": f'{{% extends "{_PREFIX}_base.html" %}}'
            "{% block b %}{{ children }}{% endblock %}"
        },
        templates={
            "base.html": f"<div>{_GUARD}{{% block b %}}{{% endblock %}}{_END}</div>"
        },
    ),
    _Leak(
        "firstof",
        CHILDREN_BODY,
        components={"guarded": f'{_GUARD}{{% firstof children "none" %}}{_END}'},
    ),
    _Leak(
        "prop-forwarding",
        CHILDREN_BODY,
        components={
            "guarded": '{% component "show" text=children %}',
            "show": f"{_GUARD}{{{{ text }}}}{_END}",
        },
    ),
    _Leak(
        "else-branch",
        f'{_GUARD}granted{{% else %}}{{% zone "ez" %}}SECRET{{% endzone %}}{_END}',
        "ez",
        holder_only=False,
    ),
    _Leak(
        "variable-perm",
        f'{{% with p="{PERM}" %}}{{% has_perm p %}}{{% zone "vz" %}}SECRET'
        f"{{% endzone %}}{_END}{{% endwith %}}",
        "vz",
    ),
    _Leak(
        "nested-blocks",
        f'{_GUARD}{{% has_perm "auth.change_user" %}}inner{{% else %}}'
        f'{{% zone "nz" %}}SECRET{{% endzone %}}{_END}{_END}',
        "nz",
    ),
    _Leak(
        "if-perms",
        '{% if perms.auth.view_user %}{% zone "pz" %}SECRET{% endzone %}{% endif %}',
        "pz",
        holder_only=False,
    ),
    _Leak(
        "for",
        '{% for i in "a" %}{% zone "fz" %}SECRET{% endzone %}{% endfor %}',
        "fz",
        holder_only=False,
    ),
)


def _named(text: str, prefix: str) -> str:
    return text.replace(_PREFIX, prefix)


def _leak_tree(tmp_path: Path, leak: _Leak, findable_templates) -> Path:
    pages = layout_root(tmp_path)
    for name, text in leak.templates.items():
        findable_templates(name, text)
    for name, text in leak.components.items():
        component_template(pages, name, _named(text, tmp_path.name))
    if leak.layout:
        (pages / "leak").mkdir()
        (pages / "leak" / "layout.djx").write_text(f"{_GUARD}{{% template %}}{_END}")
    templated_page(pages / "leak", "x = 1\n", leak.body)
    return pages


_KINDS = ["anonymous", "visitor", "holder", "superuser"]
_HOLDERS = frozenset({"holder", "superuser"})


@pytest.mark.django_db()
class TestNoGuardedZoneLeaks:
    """A partial request serves a zone a check decides only to the user it admits."""

    @pytest.mark.parametrize("leak", _LEAKS, ids=lambda leak: leak.id)
    @pytest.mark.parametrize("kind", _KINDS)
    def test_the_zone_is_unknown(
        self, tmp_path: Path, findable_templates, leak: _Leak, kind: str
    ) -> None:
        pages = _leak_tree(tmp_path, leak, findable_templates)
        client = client_as(kind)
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client.get_zones("/leak/", leak.zone)
        assert response.status_code == 400
        assert response.content == b"unknown zone"

    @pytest.mark.parametrize(
        "leak", [leak for leak in _LEAKS if leak.holder_only], ids=lambda leak: leak.id
    )
    @pytest.mark.parametrize(("kind", "shown"), [("visitor", False), ("holder", True)])
    def test_the_full_page_shows_the_secret_to_the_holder_only(
        self, tmp_path: Path, findable_templates, leak: _Leak, kind: str, shown
    ) -> None:
        pages = _leak_tree(tmp_path, leak, findable_templates)
        client = client_as(kind)
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client.get("/leak/")
        assert response.status_code == 200
        assert (b"SECRET" in response.content) is shown

    @pytest.mark.parametrize(
        ("kind", "answer"), [("visitor", b"NO"), ("holder", b"YES")]
    )
    def test_a_zone_holding_the_tag_renders_per_user(
        self, tmp_path: Path, kind: str, answer: bytes
    ) -> None:
        pages = layout_root(tmp_path)
        body = f'{{% zone "z" %}}{_GUARD}YES{{% else %}}NO{_END}{{% endzone %}}'
        templated_page(pages / "leak", "x = 1\n", body)
        client = client_as(kind)
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client.get_zones("/leak/", "z")
        assert response.status_code == 200
        assert answer in response.content


_FORM_PAGE = """
from django import forms
from django.http import HttpResponse

from next.forms import Form, action


class GuardedZoneForm(Form):
    name = forms.CharField(min_length=3)

    class Meta:
        abstract = True


@action("guarded_zone_form", form_class=GuardedZoneForm)
def save(form: GuardedZoneForm) -> HttpResponse:
    return HttpResponse("saved")
"""

_FORM_BODY = (
    '<p>public</p>{% zone "pub" %}{% form "guarded_zone_form" %}{{ form }}'
    "{% endform %}{% endzone %}"
    f'{_GUARD}{{% zone "secret" %}}SECRET{{% endzone %}}{_END}'
)


@pytest.mark.django_db()
class TestFormReRender:
    """An invalid form naming a zone a block holds morphs the form from the page."""

    def _response(self, tmp_path: Path, kind: str):
        pages = layout_root(tmp_path)
        templated_page(pages / "leak", _FORM_PAGE, _FORM_BODY)
        client = client_as(kind)
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            assert client.get("/leak/").status_code == 200
            return client.post_action(
                "guarded_zone_form",
                {"name": "x"},
                origin="/leak/",
                partial=True,
                zones="secret",
            )

    @pytest.mark.parametrize("kind", ["holder", "visitor"])
    def test_the_form_morphs_from_the_full_page(self, tmp_path: Path, kind) -> None:
        response = self._response(tmp_path, kind)
        envelope = envelope_of(response)
        assert response.status_code == 200
        assert envelope.zone_targets() == []
        assert len(envelope.form_targets()) == 1
        assert (b"SECRET" in response.content) is (kind == "holder")


def _page(tmp_path: Path, body: str) -> Path:
    return templated_page(layout_root(tmp_path) / "guarded", "x = 1\n", body)


class TestZoneInfoStandalone:
    """A zone renders standalone unless a check or a condition sits above it."""

    def test_flags(self, tmp_path: Path) -> None:
        body = (
            _GUARDED
            + '{% if c %}{% zone "cond" %}x{% endzone %}{% endif %}'
            + f'{{% zone "holds" %}}{_GUARD}x{_END}{{% endzone %}}'
        )
        leaf = _page(tmp_path, body)
        zones = zones_of(page.composed_template_for(leaf), leaf)
        assert {name: info.standalone for name, info in zones.items()} == {
            "pub": True,
            "secret": False,
            "cond": False,
            "holds": True,
        }

    def test_a_page_without_zones_reads_no_component(self, tmp_path: Path) -> None:
        leaf = _page(tmp_path, wrap("ghost", "x"))
        assert zones_of(page.composed_template_for(leaf), leaf) == {}


class TestRenderZone:
    """`render_zone` drops a bound name from a batch and refuses one asked alone."""

    @pytest.mark.parametrize("held", [(PERM,), ()], ids=["holder", "visitor"])
    def test_a_bound_name_drops_out_of_a_batch(self, tmp_path: Path, held) -> None:
        leaf = _page(tmp_path, _GUARDED)
        request = build_zone_request("secret,pub")
        request.user = PermissionHolder(*held)
        result = render_zone(leaf, ("secret", "pub"), request)
        assert list(result.html) == ["pub"]
        assert "SECRET" not in result.html["pub"]

    @pytest.mark.parametrize(
        ("body", "name"),
        [
            pytest.param(
                '{% if c %}{% zone "cond" %}x{% endzone %}{% endif %}', "cond", id="if"
            ),
            pytest.param(
                f'{_GUARD}{{% zone "cond" %}}x{{% endzone %}}{_END}',
                "cond",
                id="has-perm",
            ),
        ],
    )
    def test_a_bound_name_alone_raises(self, tmp_path: Path, body: str, name) -> None:
        leaf = _page(tmp_path, body)
        with pytest.raises(UnknownZoneError) as caught:
            render_zone(leaf, (name,), HttpRequest())
        assert caught.value.zone_name == name
        assert caught.value.declared == ()


class TestFormReRenderTarget:
    """A form re-render never targets a zone a block holds."""

    def test_declared_zones_leave_the_block_out(self, tmp_path: Path) -> None:
        declared = _declared_zones(_page(tmp_path, _GUARDED))
        assert declared == frozenset({"pub"})

    @pytest.mark.parametrize(
        ("zones", "expected"),
        [
            pytest.param("secret", None, id="held-by-a-block"),
            pytest.param("secret,pub", "pub", id="first-open"),
            pytest.param("pub", "pub", id="open"),
        ],
    )
    def test_form_zone(self, tmp_path: Path, zones: str, expected) -> None:
        leaf = _page(tmp_path, _GUARDED)
        assert _form_zone(build_zone_request(zones), leaf) == expected


class TestMorphZone:
    """`Patches.morph_zone` refuses a zone a block holds, whoever asks."""

    @pytest.fixture()
    def patches_for(self, tmp_path: Path, monkeypatch):
        leaf = _page(tmp_path, _GUARDED)
        match = OriginMatch(page_path=leaf, url_kwargs={}, origin="/guarded/")
        monkeypatch.setattr(patches_module, "resolve_origin", lambda _request: match)

        def build(*held: str) -> Patches:
            request = partial_request(origin="/guarded/")
            request.user = PermissionHolder(*held)
            return Patches(request)

        return build

    @pytest.fixture()
    def patches(self, patches_for) -> Patches:
        return patches_for(PERM)

    @pytest.mark.parametrize("held", [(PERM,), ()], ids=["holder", "visitor"])
    def test_a_held_block_raises(self, patches_for, held) -> None:
        patches = patches_for(*held)
        with pytest.raises(UnknownZoneError):
            patches.morph_zone("secret")
        assert patches.envelope().ops == ()

    def test_an_open_zone_morphs(self, patches: Patches) -> None:
        op = patches.morph_zone("pub").envelope().ops[0].as_dict()
        assert op["target"] == {"zone": "pub"}
        assert "open" in op["html"]


@dataclass(frozen=True, slots=True)
class _FailClosed:
    """A component reference the analysis cannot follow, or one it follows safely."""

    id: str
    component: str | None
    status: int


_FAIL_CLOSED = (
    _FailClosed("unresolved", None, 400),
    _FailClosed("variable-include", "<div>{% include part %}{{ children }}</div>", 400),
    _FailClosed("variable-extends", "{% extends base %}", 400),
    _FailClosed("missing-template", '{% include "nowhere-at-all.html" %}', 400),
    _FailClosed("does-not-compile", "<div>{% if %}{{ children }}</div>", 400),
    _FailClosed(
        "cycle",
        '<div>{% #component "card" %}x{% /component %}{{ children }}</div>',
        200,
    ),
)


@pytest.mark.django_db()
class TestFailClosed:
    """A component the analysis cannot follow refuses the zones of its body."""

    @pytest.mark.parametrize("case", _FAIL_CLOSED, ids=lambda case: case.id)
    def test_status(self, tmp_path: Path, case: _FailClosed) -> None:
        pages = layout_root(tmp_path)
        if case.component is not None:
            component_template(pages, "card", case.component)
        templated_page(pages / "leak", "x = 1\n", wrap("card"))
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client_as("visitor").get_zones("/leak/", "cz")
        assert response.status_code == case.status
        assert (b"SECRET" in response.content) is (case.status == 200)


@pytest.mark.django_db()
@pytest.mark.usefixtures("guard_tags")
class TestContextTagsInAComponent:
    """A context tag of `next.` reads as transparent, a third-party one fails closed."""

    @pytest.mark.parametrize(
        ("component", "status"),
        [
            pytest.param(
                '{% use_style "card.css" %}<div>{{ children }}</div>', 200, id="next"
            ),
            pytest.param(
                "{% load guard_tags %}{% peek %}<div>{{ children }}</div>",
                400,
                id="third-party",
            ),
        ],
    )
    def test_the_zone_in_its_children(
        self, tmp_path: Path, component: str, status: int
    ) -> None:
        pages = layout_root(tmp_path)
        component_template(pages, "card", component)
        templated_page(pages / "leak", "x = 1\n", wrap("card"))
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client_as("visitor").get_zones("/leak/", "cz")
        assert response.status_code == status
        assert (b"SECRET" in response.content) is (status == 200)


_ZONE = '{% zone "tz" %}SECRET{% endzone %}'


@pytest.mark.django_db()
@pytest.mark.usefixtures("guard_tags")
class TestTagsAboveAZone:
    """A block tag the analysis cannot read above a zone refuses the zone alone."""

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            pytest.param(
                f"{{% staffonly %}}{_ZONE}{{% endstaffonly %}}", 400, id="tag"
            ),
            pytest.param(
                f"{{% spaceless %}}{_ZONE}{{% endspaceless %}}", 200, id="django"
            ),
        ],
    )
    @pytest.mark.parametrize("kind", ["anonymous", "holder"])
    def test_the_zone(self, tmp_path: Path, body: str, status: int, kind: str) -> None:
        pages = layout_root(tmp_path)
        templated_page(pages / "leak", "x = 1\n", "{% load guard_tags %}" + body)
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client_as(kind).get_zones("/leak/", "tz")
        assert response.status_code == status
        assert (b"SECRET" in response.content) is (status == 200)

    @pytest.mark.parametrize("kind", ["anonymous", "holder"])
    def test_the_full_page_still_asks_the_tag(self, tmp_path: Path, kind: str) -> None:
        pages = layout_root(tmp_path)
        body = f"{{% load guard_tags %}}{{% staffonly %}}{_ZONE}{{% endstaffonly %}}"
        templated_page(pages / "leak", "x = 1\n", body)
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client_as(kind).get("/leak/")
        assert (b"SECRET" in response.content) is (kind == "holder")


@pytest.mark.django_db()
class TestEditsUnderDebug:
    """With edits watched, a component turning into a guard refuses its zone at once."""

    @pytest.mark.parametrize(("debug", "after"), [(True, 400), (False, 200)])
    def test_an_edited_component(self, tmp_path: Path, debug, after: int) -> None:
        pages = layout_root(tmp_path)
        card = component_template(pages, "card", "<div>{{ children }}</div>")
        templated_page(pages / "leak", "x = 1\n", wrap("card"))
        client = client_as("visitor")
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            override_settings(DEBUG=debug),
        ):
            before = client.get_zones("/leak/", "cz")
            touch_later(card, GUARDED_CHILDREN)
            edited = client.get_zones("/leak/", "cz")
        assert before.status_code == 200
        assert b"SECRET" in before.content
        assert edited.status_code == after

    def test_the_edit_registers_the_zones_once(self, tmp_path: Path, settings) -> None:
        pages = layout_root(tmp_path)
        card = component_template(pages, "card", "<div>{{ children }}</div>")
        leaf = templated_page(pages / "leak", "x = 1\n", wrap("card"))
        settings.DEBUG = True
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            assert NextClient().get("/leak/").status_code == 200
            template = page.composed_template_for(leaf)
            with capture_signals(zone_registered) as recorder:
                first = zones_of(template, leaf)
                assert zones_of(template, leaf) is first
                touch_later(card, GUARDED_CHILDREN)
                edited = zones_of(template, leaf)
        assert first["cz"].standalone is True
        assert edited["cz"].standalone is False
        assert len(recorder.events_for(zone_registered)) == 1

    def test_a_page_without_components_is_never_re_read(
        self, tmp_path: Path, settings
    ) -> None:
        leaf = _page(tmp_path, _GUARDED)
        settings.DEBUG = True
        template = page.composed_template_for(leaf)
        assert zones_of(template, leaf) is zones_of(template, leaf)


_PAGE_RULES = {
    "page": f'from next.pages import page\n\npage.permission_required("{PERM}")\n',
    "inherited": None,
}
_OPEN_ZONE = '<p>public</p>{% zone "pub" %}<i>open</i>{% endzone %}'


def _ruled_page(tmp_path: Path, where: str) -> Path:
    pages = layout_root(tmp_path)
    source = _PAGE_RULES[where]
    if source is None:
        write_guard(pages, f'page.permission_required("{PERM}", inherit=True)\n')
        source = "x = 1\n"
    return templated_page(pages / "ruled", source, _OPEN_ZONE)


def _zone_request(user: object) -> HttpRequest:
    request = partial_request(origin="/ruled/")
    request.user = user
    return request


_USERS = [
    pytest.param(AnonymousUser(), 302, id="anonymous"),
    pytest.param(PermissionHolder(), 403, id="visitor"),
    pytest.param(PermissionHolder(PERM), None, id="holder"),
]


@pytest.mark.django_db()
class TestZoneUnderPageAccess:
    """A zone of a page with an access rule is served only to whom the page admits."""

    @pytest.mark.parametrize("where", list(_PAGE_RULES))
    @pytest.mark.parametrize(
        ("kind", "status"), [("anonymous", 302), ("visitor", 403), ("holder", 200)]
    )
    def test_a_zone_request(
        self, tmp_path: Path, where: str, kind: str, status
    ) -> None:
        leaf = _ruled_page(tmp_path, where)
        with routed(leaf.parent.parent, COMPONENT_BACKENDS=COMPONENTS):
            response = client_as(kind).get_zones("/ruled/", "pub")
        assert response.status_code == status
        assert (b"open" in response.content) is (status == 200)

    @pytest.mark.parametrize("where", list(_PAGE_RULES))
    @pytest.mark.parametrize(("user", "status"), _USERS)
    def test_a_foreign_morph(self, tmp_path: Path, where: str, user, status) -> None:
        leaf = _ruled_page(tmp_path, where)
        patches = Patches(_zone_request(user))
        if status is None:
            op = patches.morph(zone="pub", page=leaf).envelope().ops[0].as_dict()
            assert "open" in op["html"]
            return
        with pytest.raises(ForeignPageNotAuthorizedError) as caught:
            patches.morph(zone="pub", page=leaf)
        assert caught.value.status_code == status
        assert patches.envelope().ops == ()

    @pytest.mark.parametrize(
        ("source", "user", "status"),
        [
            pytest.param("x = 1\n", AnonymousUser(), 302, id="closed-anonymous"),
            pytest.param("x = 1\n", PermissionHolder(), None, id="closed-signed-in"),
            pytest.param(
                f"{PAGE_HEAD}page.public()\n", AnonymousUser(), None, id="public"
            ),
        ],
    )
    @pytest.mark.parametrize(
        "framing",
        [login_required_middleware, login_required_setting],
        ids=["middleware", "setting"],
    )
    def test_a_foreign_morph_under_the_default_login(
        self, tmp_path: Path, source: str, user, status, framing
    ) -> None:
        leaf = templated_page(layout_root(tmp_path) / "ruled", source, _OPEN_ZONE)
        patches = Patches(_zone_request(user))
        with framing():
            if status is None:
                op = patches.morph(zone="pub", page=leaf).envelope().ops[0]
                assert "open" in op.as_dict()["html"]
                return
            with pytest.raises(ForeignPageNotAuthorizedError) as caught:
                patches.morph(zone="pub", page=leaf)
        assert caught.value.status_code == status

    @pytest.mark.parametrize(
        ("trail", "status"),
        [("ruled", None), ("ruled/admin", 302)],
        ids=["lifted", "guarded-again"],
    )
    def test_a_foreign_morph_in_a_lifted_subtree(
        self, tmp_path: Path, trail: str, status
    ) -> None:
        pages = layout_root(tmp_path)
        write_guard(pages, f'page.permission_required("{PERM}", inherit=True)\n')
        lifted = f"{PAGE_HEAD}page.public(inherit=True)\n"
        guarded = f"{PAGE_HEAD}page.login_required(inherit=True)\n"
        templated_page(pages / "ruled", lifted, _OPEN_ZONE)
        templated_page(pages / "ruled" / "admin", guarded, _OPEN_ZONE)
        leaf = pages / trail / "page.py"
        patches = Patches(_zone_request(AnonymousUser()))
        if status is None:
            op = patches.morph(zone="pub", page=leaf).envelope().ops[0]
            assert "open" in op.as_dict()["html"]
            return
        with pytest.raises(ForeignPageNotAuthorizedError) as caught:
            patches.morph(zone="pub", page=leaf)
        assert caught.value.status_code == status

    @pytest.mark.parametrize(("user", "status"), _USERS)
    def test_an_origin_morph(self, tmp_path: Path, monkeypatch, user, status) -> None:
        leaf = _ruled_page(tmp_path, "page")
        match = OriginMatch(page_path=leaf, url_kwargs={}, origin="/ruled/")
        monkeypatch.setattr(patches_module, "resolve_origin", lambda _request: match)
        patches = Patches(_zone_request(user))
        if status is None:
            assert "open" in patches.morph_zone("pub").envelope().ops[0].html
            return
        with pytest.raises(ForeignPageNotAuthorizedError) as caught:
            patches.morph_zone("pub")
        assert caught.value.status_code == status


@pytest.mark.django_db()
class TestBrokenPageUnderAMorph:
    """A morph over a page whose chain fails to import raises it, never a 404."""

    @pytest.mark.parametrize("where", ["page", "above"])
    @pytest.mark.parametrize("morph", ["foreign", "origin"])
    def test_the_import_error(
        self, tmp_path: Path, monkeypatch, settings, where: str, morph: str
    ) -> None:
        settings.DEBUG = False
        pages = layout_root(tmp_path)
        failing = "raise RuntimeError('boom')\n"
        if where == "above":
            broken = write_guard(pages, failing)
            leaf = templated_page(pages / "ruled", "x = 1\n", _OPEN_ZONE)
        else:
            leaf = broken = templated_page(pages / "ruled", failing, _OPEN_ZONE)
        match = OriginMatch(page_path=leaf, url_kwargs={}, origin="/ruled/")
        monkeypatch.setattr(patches_module, "resolve_origin", lambda _request: match)
        patches = Patches(_zone_request(PermissionHolder()))
        run = {
            "foreign": lambda: patches.morph(zone="pub", page=leaf),
            "origin": lambda: patches.morph_zone("pub"),
        }[morph]
        with pytest.raises(PageModuleImportError) as caught:
            run()
        assert caught.value.file_path == broken
        assert patches.envelope().ops == ()


_PART = f"{_PREFIX}_part.html"
_BASE = f"{_PREFIX}_base.html"
_SHELL = (
    '{% if ok %}{% set_slot "nav" %}{% endif %}<main>{% set_slot "content" %}</main>'
)
_RENDER_MODULE = (
    "def render(children, request=None):\n"
    "    user = getattr(request, 'user', None)\n"
    "    held = user is not None and user.has_perm('auth.view_user')\n"
    "    return children if held else ''\n"
)


@dataclass(frozen=True, slots=True)
class _Probe:
    """A page shape and the status its zone answers to every user."""

    leak: _Leak
    status: int
    module: str | None = None


def _decided(id_: str, body: str = CHILDREN_BODY, **kwargs) -> _Probe:
    return _Probe(_Leak(id_, body, **kwargs), 400)


_PROBES = (
    _decided(
        "if-children", components={"guarded": "{% if ok %}{{ children }}{% endif %}"}
    ),
    _decided(
        "if-slot",
        fill_slot("content"),
        components={"guarded": '{% if ok %}{% set_slot "content" %}{% endif %}'},
    ),
    _decided(
        "for-children",
        components={"guarded": "{% for i in xs %}{{ children }}{% endfor %}"},
    ),
    _decided(
        "with-alias-if",
        components={
            "guarded": "{% with c=children %}{% if ok %}{{ c }}{% endif %}{% endwith %}"
        },
    ),
    _decided(
        "include-if",
        components={"guarded": f'{{% if ok %}}{{% include "{_PART}" %}}{{% endif %}}'},
        templates={"part.html": "{{ children }}"},
    ),
    _decided(
        "include-with",
        components={"guarded": f'{{% include "{_PART}" with c=children %}}'},
        templates={"part.html": "{% if ok %}{{ c }}{% endif %}"},
    ),
    _decided(
        "extends-if",
        components={
            "guarded": f'{{% extends "{_BASE}" %}}'
            "{% block b %}{{ children }}{% endblock %}"
        },
        templates={"base.html": "{% if ok %}{% block b %}{% endblock %}{% endif %}"},
    ),
    _decided(
        "nested-if",
        components={
            "guarded": wrap("inner", "{{ children }}"),
            "inner": "{% if ok %}{{ children }}{% endif %}",
        },
    ),
    _decided(
        "prop-if",
        components={
            "guarded": '{% component "show" text=children %}',
            "show": "{% if ok %}{{ text }}{% endif %}",
        },
    ),
    _decided(
        "inherited-slot",
        fill_slot("content"),
        components={
            "guarded": '{% component "inner" %}',
            "inner": '{% if ok %}{% set_slot "content" %}{% endif %}',
        },
    ),
    _decided("default-filter", components={"guarded": "{{ hidden|default:children }}"}),
    _decided(
        "shadow",
        components={"guarded": "{% with children=flag %}{{ children }}{% endwith %}"},
    ),
    _decided("firstof-flag", components={"guarded": "{% firstof flag children %}"}),
    _decided("shell-nav", fill_slot("nav"), components={"guarded": _SHELL}),
    _Probe(
        _Leak("render-function", CHILDREN_BODY, components={"guarded": "<div/>"}),
        400,
        module=_RENDER_MODULE,
    ),
    _Probe(
        _Leak(
            "lazy",
            f'{_GUARD}{{% zone "lzz" lazy="load" %}}SECRET{{% placeholder %}}wait'
            f"{{% endzone %}}{_END}",
            "lzz",
        ),
        400,
    ),
    _Probe(
        _Leak("shell-content", fill_slot("content"), components={"guarded": _SHELL}),
        200,
    ),
    _Probe(
        _Leak(
            "card-like",
            CHILDREN_BODY,
            components={
                "guarded": "{% if title %}<h3>{{ title }}</h3>{% endif %}"
                "<div>{{ children }}</div>"
            },
        ),
        200,
    ),
)


@pytest.mark.django_db()
class TestProbeShapes:
    """No page shape answers a zone with the body of a block the user is refused."""

    @pytest.mark.parametrize("probe", _PROBES, ids=lambda probe: probe.leak.id)
    @pytest.mark.parametrize("kind", _KINDS)
    def test_the_zone(
        self, tmp_path: Path, findable_templates, probe: _Probe, kind: str
    ) -> None:
        pages = _leak_tree(tmp_path, probe.leak, findable_templates)
        if probe.module is not None:
            (pages / "_components" / "guarded" / "component.py").write_text(
                probe.module
            )
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            response = client_as(kind).get_zones("/leak/", probe.leak.zone)
        assert response.status_code == probe.status
        assert (b"SECRET" in response.content) is (probe.status == 200)
