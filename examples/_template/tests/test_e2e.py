def test_home_page_renders(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert b"Welcome to the next.dj template." in response.content
