from dataclasses import FrozenInstanceError

import pytest

from next.partial import Patches
from next.realtime import Message, Position
from tests.support.realtime import message_payload


def test_message_dict_round_trip_preserves_envelope_and_epoch() -> None:
    envelope = Patches(None, echo_of="r1").refresh(zone="results").envelope()
    message = Message("poll:1", 3, envelope, 123.5, "epoch-a")

    assert message.as_dict()["epoch"] == "epoch-a"
    assert Message.from_dict(message.as_dict()) == message


def test_message_without_epoch_is_slotted_with_empty_epoch() -> None:
    message = Message("poll:1", 1, Patches(None).envelope(), 1.0)

    assert not hasattr(message, "__dict__")
    assert message.epoch == ""


def test_position_assignment_raises_frozen_error() -> None:
    position = Position(epoch="e1", seq=2)

    assert not hasattr(position, "__dict__")
    with pytest.raises(FrozenInstanceError):
        position.seq = 3


def test_message_from_dict_missing_epoch_defaults_to_empty() -> None:
    data = message_payload()
    del data["epoch"]

    assert Message.from_dict(data).epoch == ""


def test_message_from_dict_integer_timestamp_becomes_float() -> None:
    assert Message.from_dict(message_payload(published_at=2)).published_at == 2.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"topic": 1},
        {"seq": "1"},
        {"seq": True},
        {"epoch": 7},
        {"published_at": "now"},
        {"published_at": False},
        {"envelope": []},
        {"envelope": {"ops": []}},
        {"envelope": {"version": "v1", "ops": [1]}},
    ],
)
def test_message_from_dict_wrong_wire_type_raises(overrides: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        Message.from_dict(message_payload(**overrides))


@pytest.mark.parametrize("seq", [0, -1])
def test_message_from_dict_non_positive_seq_raises(seq: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        Message.from_dict(message_payload(seq=seq))
