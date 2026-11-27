import pytest

from next.defer.tokens.carrier import (
    CARRIERS,
    BodyCarrier,
    CarrierPlace,
    HeaderCarrier,
    QueryCarrier,
)


class TestDescriptors:
    """Each carrier tells the client its place and the name it carries the token in."""

    @pytest.mark.parametrize(
        ("carrier", "attribute", "payload"),
        [
            (
                HeaderCarrier(),
                "header:X-Next-Boundary",
                {"place": "header", "name": "X-Next-Boundary"},
            ),
            (QueryCarrier(), "query:_nb", {"place": "query", "name": "_nb"}),
            (BodyCarrier(), "body:_nb", {"place": "body", "name": "_nb"}),
        ],
    )
    def test_the_descriptor_serialises(self, carrier, attribute, payload) -> None:
        descriptor = carrier.describe()
        assert isinstance(descriptor, CarrierPlace)
        assert descriptor.attribute == attribute
        assert descriptor.as_dict() == payload


class TestExtract:
    """Each carrier reads the token from its own place only."""

    def test_the_header_carrier_reads_the_header(self, rf) -> None:
        request = rf.get("/?_nb=query", HTTP_X_NEXT_BOUNDARY="tok")
        assert HeaderCarrier().extract(request) == "tok"
        assert HeaderCarrier().extract(rf.get("/?_nb=q")) is None
        assert "headers" not in vars(request)

    def test_an_empty_header_carries_no_token(self, rf) -> None:
        request = rf.get("/", HTTP_X_NEXT_BOUNDARY="")
        assert HeaderCarrier().extract(request) is None

    def test_the_body_carrier_reads_the_form_body(self, rf) -> None:
        request = rf.post("/?_nb=query", {"_nb": "tok"})
        assert BodyCarrier().extract(request) == "tok"
        assert BodyCarrier().extract(rf.post("/?_nb=q")) is None

    def test_the_query_carrier_reads_the_query(self, rf) -> None:
        request = rf.get("/?_nb=tok", HTTP_X_NEXT_BOUNDARY="header")
        assert QueryCarrier().extract(request) == "tok"
        assert QueryCarrier().extract(rf.get("/")) is None

    def test_a_repeated_field_joins_into_text_no_token_parses_as(self, rf) -> None:
        request = rf.get("/?_nb=a&_nb=b")
        assert QueryCarrier().extract(request) == "a,b"


class TestChoice:
    """`CARRIER` names one of the places the client runtime knows."""

    def test_each_place_names_its_carrier(self) -> None:
        assert {name: carrier.place for name, carrier in CARRIERS.items()} == {
            "header": "header",
            "query": "query",
            "body": "body",
        }
        assert BodyCarrier.methods == ("GET", "HEAD", "POST")
        assert HeaderCarrier.methods == QueryCarrier.methods == ("GET", "HEAD")
