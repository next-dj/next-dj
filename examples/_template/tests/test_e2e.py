"""Scaffold tests.

To assert that a framework signal fired during a request, add a test
that wraps ``client.get(...)`` with ``SignalRecorder`` from
``next.testing``:

    def test_home_emits_page_rendered(client):
        recorder = SignalRecorder(page_rendered)
        with recorder:
            client.get("/")
        assert len(recorder.events) == 1
"""


def test_home_page_renders(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert b"Welcome to the next.dj template." in response.content
