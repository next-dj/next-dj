import pytest

from next.realtime import InvalidTopicError
from next.realtime.topics import validate_topic


@pytest.mark.parametrize("topic", ["room:17", "poll.results", "a_b-c", "x" * 200])
def test_valid_topic_names_are_returned(topic: str) -> None:
    assert validate_topic(topic) == topic


@pytest.mark.parametrize("topic", ["", "next:internal", "bad topic", "x" * 201, 1])
def test_invalid_topic_names_raise(topic: object) -> None:
    with pytest.raises(InvalidTopicError) as error:
        validate_topic(topic)
    assert error.value.topic == topic
