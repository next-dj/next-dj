from collections.abc import Iterator
from unittest import mock

import pytest
from django.contrib.auth.models import Group, Permission
from django.http import HttpResponse

from next.pages import page
from next.pages.manager import (
    bodies as manager_bodies,
    gating as manager_gating,
    render as manager_render,
)
from next.pages.signals import page_rendered
from next.testing import capture_signals, envelope_of
from next.views import pages as view_pages
from tests.support import (
    bound_dependency,
    build_page_request,
    build_zone_request,
    unified_view,
    write_page,
)
from tests.support.views import write_dynamic_page


_ASYNC_STATIC = (
    "import asyncio\n"
    "from next.pages import context\n\n"
    "template = '<p>{{ first }} {{ second }}</p>"
    '{% zone "z" %}<i>{{ zoned }}</i>{% endzone %}\'\n\n'
    "@context('first')\n"
    "async def first():\n"
    "    await asyncio.sleep(0)\n"
    "    return 'A'\n\n"
    "@context('second')\n"
    "def second(first):\n"
    "    return first + 'B'\n\n"
    "@context('zoned', zone='z')\n"
    "async def zoned(first):\n"
    "    return 'Z' + first\n"
)

_SYNC_STATIC = (
    "from next.pages import context\n\n"
    "template = '<p>{{ value }}</p>{% zone \"z\" %}<i>{{ value }}</i>{% endzone %}'\n\n"
    "@context('value')\n"
    "def value():\n"
    "    return 'sync'\n"
)

_ASYNC_RENDER = (
    "import asyncio\n"
    "from next.pages import context\n\n"
    "async def render(request):\n"
    "    await asyncio.sleep(0)\n"
    "    return '<b>{{ label }}</b>'\n\n"
    "@context('label')\n"
    "async def label():\n"
    "    return 'async'\n"
)

_SLOW_CONTEXT = (
    "import asyncio\n"
    "from next.pages import context\n\n"
    "async def render():\n"
    "    return '<b>{{ label }}</b>'\n\n"
    "@context('label')\n"
    "async def label():\n"
    "    await asyncio.sleep(0.05)\n"
    "    return 'slow'\n"
)

_SYNC_OBJECT = (
    "from django.contrib.auth.models import Permission\n"
    "from next.deps import DObject\n"
    "from next.pages import context\n\n"
    "template = '<p>{{ codename }}</p>'\n\n"
    "@context('codename')\n"
    "def codename(permission: DObject[Permission]):\n"
    "    return permission.codename\n"
)

_ASYNC_REDIRECT = (
    "from django.shortcuts import redirect\n\n"
    "async def render(request):\n"
    "    return redirect('/elsewhere/')\n"
)

_ASYNC_RENDER_SYNC_CONTEXT = (
    "from next.pages import context\n\n"
    "async def render():\n"
    "    return '<b>{{ label }}</b>'\n\n"
    "@context('label')\n"
    "def label():\n"
    "    return 'sync'\n"
)

_ASYNC_POST = (
    "from django.contrib.auth.models import Group\n"
    "from django.db import connection\n"
    "from next.pages import context\n\n"
    "template = '{{ a_made }} {{ b_seen }}'\n\n"
    "@context('a_made')\n"
    "def a_made():\n"
    "    Group.objects.create(name='draft')\n"
    "    return connection.in_atomic_block\n\n"
    "@context('b_seen')\n"
    "async def b_seen(a_made):\n"
    "    group = await Group.objects.aget(name='draft')\n"
    "    return group.name\n"
)


@pytest.fixture()
def never_driven() -> Iterator[None]:
    """Fail any entry into the async bridge."""
    with (
        mock.patch.object(view_pages, "drive", side_effect=AssertionError),
        mock.patch.object(manager_bodies, "bridged_scope", side_effect=AssertionError),
        mock.patch.object(manager_gating, "bridged_scope", side_effect=AssertionError),
        mock.patch.object(manager_render, "bridged_scope", side_effect=AssertionError),
    ):
        yield


class TestAsyncPages:
    """The sync view drives the async part of a page once, then renders the template."""

    def test_a_static_page_awaits_its_context(self, tmp_path) -> None:
        view = unified_view(page, write_page(tmp_path, source=_ASYNC_STATIC))

        response = view(build_page_request())

        assert response.content == (
            b'<p>A AB</p><div data-next-zone="z"><i>ZA</i></div>'
        )

    def test_a_static_page_builds_its_context_through_the_bridge_once(
        self, tmp_path
    ) -> None:
        view = unified_view(page, write_page(tmp_path, source=_ASYNC_STATIC))

        with mock.patch(
            "next.pages.manager.render.bridged_scope",
            wraps=manager_render.bridged_scope,
        ) as bridged:
            view(build_page_request())

        assert bridged.call_count == 1

    def test_a_zone_get_awaits_the_zone_context(self, tmp_path) -> None:
        view = unified_view(page, write_page(tmp_path, source=_ASYNC_STATIC))
        view(build_zone_request("z"))

        with mock.patch.object(page, "awaits", wraps=page.awaits) as awaits:
            response = view(build_zone_request("z"))

        assert envelope_of(response).html_for_zone("z") == (
            '<div data-next-zone="z"><i>ZA</i></div>'
        )
        assert awaits.call_count == 1

    def test_the_render_time_of_an_async_render_includes_its_context(
        self, tmp_path
    ) -> None:
        view = unified_view(page, write_page(tmp_path, source=_SLOW_CONTEXT))

        with capture_signals(page_rendered) as recorder:
            view(build_page_request())

        assert recorder.events[0].kwargs["duration_ms"] >= 40

    @pytest.mark.usefixtures("never_driven")
    def test_a_sync_page_never_enters_the_async_path(self, tmp_path) -> None:
        static = unified_view(
            page, write_page(tmp_path / "static", source=_SYNC_STATIC)
        )
        dynamic = unified_view(page, write_dynamic_page(tmp_path / "dynamic"))

        with mock.patch.object(page, "arender_context", side_effect=AssertionError):
            full = static(build_page_request())
            zone = static(build_zone_request("z"))
            body = dynamic(build_page_request())

        assert full.content == b'<p>sync</p><div data-next-zone="z"><i>sync</i></div>'
        assert envelope_of(zone).html_for_zone("z") == (
            '<div data-next-zone="z"><i>sync</i></div>'
        )
        assert body.content == b"<p>dynamic</p>"

    @pytest.mark.django_db()
    @pytest.mark.usefixtures("scoped_models")
    @pytest.mark.usefixtures("never_driven")
    def test_a_sync_page_taking_an_object_never_drives(self, tmp_path) -> None:
        permission = Permission.objects.filter(content_type__app_label="auth").first()
        page_file = write_page(tmp_path, source=_SYNC_OBJECT)
        view = unified_view(page, page_file)

        with bound_dependency("scope", lambda: "auth"):
            response = view(build_page_request(), permission=permission.pk)

        assert page.awaits(page_file) is False
        assert response.content == f"<p>{permission.codename}</p>".encode()

    def test_an_async_page_is_driven_once(self, tmp_path) -> None:
        view = unified_view(page, write_page(tmp_path, source=_ASYNC_RENDER))

        with mock.patch.object(view_pages, "drive", wraps=view_pages.drive) as driven:
            response = view(build_page_request())

        assert response.content == b"<b>async</b>"
        assert driven.call_count == 1

    def test_an_async_render_with_a_sync_context_collects_it_after_the_drive(
        self, tmp_path
    ) -> None:
        view = unified_view(
            page, write_page(tmp_path, source=_ASYNC_RENDER_SYNC_CONTEXT)
        )

        assert view(build_page_request()).content == b"<b>sync</b>"

    def test_an_async_render_response_bypasses_the_layout(self, tmp_path) -> None:
        view = unified_view(page, write_page(tmp_path, source=_ASYNC_REDIRECT))

        response = view(build_page_request())
        tick = view(build_zone_request("z"))

        assert response.status_code == 302
        assert tick.status_code == 302

    def test_a_zone_in_an_async_dynamic_body_is_a_bad_request(self, tmp_path) -> None:
        view = unified_view(page, write_page(tmp_path, source=_ASYNC_RENDER))

        response = view(build_zone_request("z"))

        assert response.status_code == 400
        assert response.content == b"zone in dynamic body"

    @pytest.mark.usefixtures("never_driven")
    @pytest.mark.parametrize("source", [_ASYNC_STATIC, _ASYNC_RENDER])
    def test_a_refusal_answers_before_any_drive(self, tmp_path, source) -> None:
        view = unified_view(page, write_page(tmp_path, source=source))
        denial = HttpResponse(status=403)

        with mock.patch.object(view_pages, "refusal", return_value=denial):
            assert view(build_page_request()) is denial

    def test_a_module_broken_at_build_is_served_by_its_colour_now(
        self, tmp_path
    ) -> None:
        healed = write_page(tmp_path / "sync", source="template = '<p>healed</p>'\n")
        driven = write_page(tmp_path / "async", source=_ASYNC_RENDER)

        sync_view = view_pages.unified_view(page, healed, None, broken_at_build=True)
        async_view = view_pages.unified_view(page, driven, None, broken_at_build=True)

        assert sync_view(build_page_request()).content == b"<p>healed</p>"
        assert async_view(build_page_request()).content == b"<b>async</b>"

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.usefixtures("fresh_transaction_declarations")
    def test_an_async_callable_reads_rows_of_the_open_transaction(
        self, tmp_path, rf
    ) -> None:
        view = unified_view(page, write_page(tmp_path, source=_ASYNC_POST))

        response = view(rf.post("/"))

        assert response.content == b"True draft"
        assert Group.objects.filter(name="draft").exists()
