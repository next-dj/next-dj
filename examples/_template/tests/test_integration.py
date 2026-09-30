from next.testing import assert_metadata


def test_home_page_renders(next_client) -> None:
    response = next_client.get("/")
    assert response.status_code == 200
    assert b"Welcome to the next.dj template." in response.content


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
        '<link rel="icon" href="https://template.example/static/myapp/icon.svg" '
        'type="image/svg+xml">'
    ) in body
