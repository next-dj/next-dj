from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import NoReverseMatch, reverse
from myapp.models import Item

from next.testing import assert_metadata
from next.urls import page_reverse


PAGE = "/myapp/"

pytestmark = pytest.mark.django_db


def test_the_home_page_is_public(next_client) -> None:
    response = next_client.get("/")
    assert response.status_code == 200
    assert b"It works" in response.content


def test_the_head_folds_the_site_defaults(next_client) -> None:
    assert_metadata(
        next_client.get("/"),
        title="Next.dj example template",
        description=(
            "A next.dj starter with one page root, the shared UI kit and one test."
        ),
        viewport="width=device-width, initial-scale=1",
        robots=None,
    )


def test_static_names_resolve_to_urls(next_client) -> None:
    body = next_client.get("/").content.decode()
    assert '<link rel="stylesheet" href="/static/shared/css/tokens.css?v=v1">' in body
    assert '<script type="module" src="/static/shared/js/base.mjs?v=v1">' in body
    assert (
        '<link rel="icon" href="https://template.example/static/home/icon.svg" '
        'type="image/svg+xml">'
    ) in body


def test_the_route_map_names_each_page_and_its_access() -> None:
    out = StringIO()
    call_command("routes", stdout=out)
    _header, *lines = out.getvalue().splitlines()
    rows = {line.split()[0]: line.split()[1:] for line in lines}
    assert rows["/"] == ["next:page_", "home/pages/page.py", "public"]
    assert rows[PAGE] == ["myapp:page_", "myapp/pages/page.py", "login", "(default)"]


def test_the_app_tree_answers_under_its_prefix() -> None:
    assert page_reverse(namespace="myapp") == PAGE
    assert reverse("myapp:page_") == PAGE
    with pytest.raises(NoReverseMatch):
        reverse("next:page_myapp")


def test_an_anonymous_visitor_is_sent_to_sign_in(next_client) -> None:
    response = next_client.get(PAGE)
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('admin:login')}?next={PAGE}"


def test_the_page_lists_only_the_visitors_items(
    next_client, alice, mine, theirs
) -> None:
    next_client.force_login(alice)
    body = next_client.get(PAGE).content.decode()
    assert "Mine" in body
    assert "Theirs" not in body


def test_the_form_creates_an_item_for_the_visitor(next_client, alice) -> None:
    next_client.force_login(alice)
    response = next_client.post_action(
        "create_item_form", {"title": "Posted"}, origin=PAGE
    )
    assert response.status_code == 302
    assert response["Location"] == PAGE
    assert Item.objects.filter(owner=alice, title="Posted").exists()


def test_the_admin_adds_through_the_service(next_client, superuser) -> None:
    next_client.force_login(superuser)
    response = next_client.post("/admin/myapp/item/add/", {"title": "From admin"})
    assert response.status_code == 302
    assert Item.objects.get().owner == superuser


def test_the_admin_renames_through_the_service(next_client, superuser) -> None:
    next_client.force_login(superuser)
    next_client.post("/admin/myapp/item/add/", {"title": "Old"})
    item = Item.objects.get()
    url = f"/admin/myapp/item/{item.pk}/change/"
    assert next_client.post(url, {"title": "New"}).status_code == 302
    item.refresh_from_db()
    assert item.title == "New"


def test_the_admin_lists_the_items_the_superuser_sees(
    next_client, superuser, mine, theirs
) -> None:
    next_client.force_login(superuser)
    body = next_client.get("/admin/myapp/item/").content.decode()
    assert "Mine" in body
    assert "Theirs" in body
