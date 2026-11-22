import pytest

from next.suspense.tokens import (
    BodyTransport,
    HeaderTransport,
    QueryTransport,
    TransportDescriptor,
)


class TestDescriptors:
    """Each transport tells the client its place and the name it carries the token in."""

    @pytest.mark.parametrize(
        ("transport", "attribute", "payload"),
        [
            (
                HeaderTransport(),
                "header:X-Next-Boundary",
                {"place": "header", "name": "X-Next-Boundary"},
            ),
            (QueryTransport(), "query:_nb", {"place": "query", "name": "_nb"}),
            (BodyTransport(), "body:_nb", {"place": "body", "name": "_nb"}),
        ],
    )
    def test_the_descriptor_serialises(self, transport, attribute, payload) -> None:
        descriptor = transport.describe()
        assert isinstance(descriptor, TransportDescriptor)
        assert descriptor.attribute == attribute
        assert descriptor.as_dict() == payload


class TestExtract:
    """Each transport reads the token from its own place only."""

    def test_the_header_transport_reads_the_header(self, rf) -> None:
        request = rf.get("/?_nb=query", HTTP_X_NEXT_BOUNDARY="tok")
        assert HeaderTransport().extract(request) == "tok"
        assert HeaderTransport().extract(rf.get("/?_nb=q")) is None

    def test_an_empty_header_carries_no_token(self, rf) -> None:
        request = rf.get("/", HTTP_X_NEXT_BOUNDARY="")
        assert HeaderTransport().extract(request) is None

    def test_the_query_transport_reads_the_query(self, rf) -> None:
        request = rf.get("/?_nb=tok", HTTP_X_NEXT_BOUNDARY="header")
        assert QueryTransport().extract(request) == "tok"
        assert QueryTransport().extract(rf.get("/")) is None

    def test_the_body_transport_reads_the_form_body(self, rf) -> None:
        request = rf.post("/?_nb=query", {"_nb": "tok"})
        assert BodyTransport().extract(request) == "tok"
        assert BodyTransport().extract(rf.post("/?_nb=q")) is None

    def test_a_repeated_field_joins_into_text_no_token_parses_as(self, rf) -> None:
        request = rf.get("/?_nb=a&_nb=b")
        assert QueryTransport().extract(request) == "a,b"
