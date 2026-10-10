from django.http import HttpResponse, HttpResponseBase, StreamingHttpResponse

from next.testing.responses import response_text


class TestResponseText:
    """`response_text` reads a whole, a streamed, or a bodiless response."""

    def test_a_whole_body_reads_as_text(self) -> None:
        assert response_text(HttpResponse("body")) == "body"

    def test_a_streamed_body_stays_readable(self) -> None:
        response = StreamingHttpResponse(iter([b"a", b"b"]))
        assert response_text(response) == "ab"
        assert response_text(response) == "ab"
        assert response.getvalue() == b"ab"

    def test_a_response_without_a_body_reads_empty(self) -> None:
        assert response_text(HttpResponseBase()) == ""
