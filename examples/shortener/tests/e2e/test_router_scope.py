import pytest
from e2e_support.browser import assert_same_document, mark_document, wait_for_runtime
from playwright.sync_api import Page, Request, expect
from shortener.cache import pending_clicks
from shortener.models import Link


pytestmark = pytest.mark.e2e

SLUG = "scoped"


def open_home(page: Page, base_url: str) -> list[Request]:
    Link.objects.create(slug=SLUG, url=f"{base_url}/admin/")
    sent: list[Request] = []

    def record(request: Request) -> None:
        sent.append(request)

    page.on("request", record)
    with page.expect_response(lambda response: "/_next/routes/" in response.url):
        page.goto(base_url)
    wait_for_runtime(page)
    page.wait_for_load_state("networkidle")
    return sent


def short_requests(sent: list[Request]) -> list[Request]:
    return [
        request
        for request in sent
        if request.url.partition("?")[0].endswith(f"/s/{SLUG}/")
    ]


def test_hovering_a_plain_django_view_sends_no_request(
    page: Page, base_url: str
) -> None:
    sent = open_home(page, base_url)

    page.get_by_role("link", name=f"/s/{SLUG}/").hover()
    tree_link = page.get_by_role("navigation", name="Main").get_by_role(
        "link", name="admin"
    )
    with page.expect_request(
        lambda request: (
            request.url.partition("?")[0] == f"{base_url}/admin/"
            and "x-next-prefetch" in request.headers
        )
    ):
        tree_link.hover()

    assert short_requests(sent) == []
    assert SLUG not in pending_clicks()


def test_clicking_a_plain_django_view_loads_one_document_and_no_fetch(
    page: Page, base_url: str
) -> None:
    sent = open_home(page, base_url)

    page.get_by_role("link", name=f"/s/{SLUG}/").click()

    expect(page).to_have_url(f"{base_url}/admin/")
    assert [request.resource_type for request in short_requests(sent)] == ["document"]
    assert pending_clicks()[SLUG] == 1


def test_clicking_a_page_of_the_tree_navigates_in_place(
    page: Page, base_url: str
) -> None:
    sent = open_home(page, base_url)
    mark_document(page)

    page.get_by_role("navigation", name="Main").get_by_role(
        "link", name="admin"
    ).click()

    expect(page).to_have_url(f"{base_url}/admin/")
    assert_same_document(page)
    navigations = [
        request
        for request in sent
        if request.url.partition("?")[0] == f"{base_url}/admin/"
    ]
    assert navigations
    assert {request.resource_type for request in navigations} == {"fetch"}
