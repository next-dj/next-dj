"""Scaffold tests.

Native Django assertions cover most of the work. For HTML-aware matches
prefer ``assertContains(response, ..., html=True)`` or ``assertInHTML``.
For class-token checks on a specific anchor use ``find_anchor`` +
``assert_has_class`` from ``next.testing``.

To assert that a framework signal fired during a request, wrap the call
with ``capture_signals``:

    from next.pages.signals import page_rendered
    from next.testing import capture_signals

    def test_home_emits_page_rendered(client):
        with capture_signals(page_rendered) as rec:
            client.get("/")
        assert len(rec) == 1
"""


def test_home_page_renders(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert b"Welcome to the next.dj template." in response.content
