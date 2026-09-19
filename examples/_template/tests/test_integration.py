def test_home_page_renders(next_client) -> None:
    response = next_client.get("/")
    assert response.status_code == 200
    assert b"Welcome to the next.dj template." in response.content


def test_static_names_resolve_to_urls(next_client) -> None:
    body = next_client.get("/").content.decode()
    assert '<link rel="stylesheet" href="/static/shared/css/tokens.css">' in body
    assert '<script type="module" src="/static/shared/js/base.mjs">' in body
    assert '<link rel="icon" href="/static/site/favicon.svg">' in body
