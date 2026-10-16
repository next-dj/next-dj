import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import HttpRequest
from django.test import override_settings

from next.forms.origin import OriginMatch
from next.pages import page
from next.partial import Patches, UnknownZoneError, patches as patches_module
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
from tests.support import PermissionHolder, build_zone_request, partial_request, routed
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


def _client(kind: str) -> NextClient:
    client = NextClient(enforce_csrf_checks=False)
    users = get_user_model().objects
    if kind == "superuser":
        client.force_login(users.create_superuser("root", "root@example.com"))
    elif kind in {"holder", "visitor"}:
        user = users.create_user(kind)
        if kind == "holder":
            user.user_permissions.add(Permission.objects.get(codename="view_user"))
        client.force_login(user)
    return client


_KINDS = ["anonymous", "visitor", "holder", "superuser"]


@pytest.mark.django_db()
class TestNoGuardedZoneLeaks:
    """A partial request never serves a zone a check or a condition decides."""

    @pytest.mark.parametrize("leak", _LEAKS, ids=lambda leak: leak.id)
    @pytest.mark.parametrize("kind", _KINDS)
    def test_the_zone_is_unknown(
        self, tmp_path: Path, findable_templates, leak: _Leak, kind: str
    ) -> None:
        pages = _leak_tree(tmp_path, leak, findable_templates)
        client = _client(kind)
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
        client = _client(kind)
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
        client = _client(kind)
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
    """An invalid form naming a refused zone falls back, the secret never travels."""

    @pytest.mark.parametrize(("kind", "shown"), [("visitor", False), ("holder", True)])
    def test_falls_back_to_the_form(self, tmp_path: Path, kind: str, shown) -> None:
        pages = layout_root(tmp_path)
        templated_page(pages / "leak", _FORM_PAGE, _FORM_BODY)
        client = _client(kind)
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            assert client.get("/leak/").status_code == 200
            response = client.post_action(
                "guarded_zone_form",
                {"name": "x"},
                origin="/leak/",
                partial=True,
                zones="secret",
            )
        envelope = envelope_of(response)
        assert response.status_code == 200
        assert envelope.zone_targets() == []
        assert len(envelope.form_targets()) == 1
        assert (b"SECRET" in response.content) is shown


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
    """`render_zone` drops a refused name and names only the open ones."""

    def test_a_batch_drops_the_refused_name(self, tmp_path: Path) -> None:
        leaf = _page(tmp_path, _GUARDED)
        request = build_zone_request("secret,pub")
        request.user = PermissionHolder(PERM)
        result = render_zone(leaf, ("secret", "pub"), request)
        assert list(result.html) == ["pub"]

    def test_a_refused_name_alone_raises(self, tmp_path: Path) -> None:
        leaf = _page(tmp_path, _GUARDED)
        with pytest.raises(UnknownZoneError) as caught:
            render_zone(leaf, ("secret",), HttpRequest())
        assert caught.value.zone_name == "secret"
        assert caught.value.declared == ("pub",)


class TestFormReRenderTarget:
    """A form re-render never targets a refused zone, it falls back instead."""

    def test_declared_zones_leave_the_refused_one_out(self, tmp_path: Path) -> None:
        assert _declared_zones(_page(tmp_path, _GUARDED)) == frozenset({"pub"})

    @pytest.mark.parametrize(
        ("zones", "expected"),
        [
            pytest.param("secret", None, id="refused-falls-back"),
            pytest.param("secret,pub", "pub", id="next-open-zone"),
            pytest.param("pub", "pub", id="open"),
        ],
    )
    def test_form_zone(self, tmp_path: Path, zones: str, expected) -> None:
        leaf = _page(tmp_path, _GUARDED)
        assert _form_zone(build_zone_request(zones), leaf) == expected


class TestMorphZone:
    """`Patches.morph_zone` refuses a zone a check decides."""

    @pytest.fixture()
    def patches(self, tmp_path: Path, monkeypatch) -> Patches:
        leaf = _page(tmp_path, _GUARDED)
        match = OriginMatch(page_path=leaf, url_kwargs={}, origin="/guarded/")
        monkeypatch.setattr(patches_module, "resolve_origin", lambda _request: match)
        request = partial_request(origin="/guarded/")
        request.user = PermissionHolder(PERM)
        return Patches(request)

    def test_a_refused_zone_raises(self, patches: Patches) -> None:
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
            response = _client("visitor").get_zones("/leak/", "cz")
        assert response.status_code == case.status
        assert (b"SECRET" in response.content) is (case.status == 200)


def _touch_later(path: Path, text: str) -> None:
    path.write_text(text)
    future = time.time() + 5
    os.utime(path, (future, future))


@pytest.mark.django_db()
class TestEditsUnderDebug:
    """With edits watched, a component turning into a guard refuses its zone at once."""

    @pytest.mark.parametrize(("debug", "after"), [(True, 400), (False, 200)])
    def test_an_edited_component(self, tmp_path: Path, debug, after: int) -> None:
        pages = layout_root(tmp_path)
        card = component_template(pages, "card", "<div>{{ children }}</div>")
        templated_page(pages / "leak", "x = 1\n", wrap("card"))
        client = _client("visitor")
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            override_settings(DEBUG=debug),
        ):
            before = client.get_zones("/leak/", "cz")
            _touch_later(card, GUARDED_CHILDREN)
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
            # A request walks the page tree, so its components resolve by name.
            assert NextClient().get("/leak/").status_code == 200
            template = page.composed_template_for(leaf)
            with capture_signals(zone_registered) as recorder:
                first = zones_of(template, leaf)
                assert zones_of(template, leaf) is first
                _touch_later(card, GUARDED_CHILDREN)
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
