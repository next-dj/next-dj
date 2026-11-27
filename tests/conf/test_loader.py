import pytest
from django.core.exceptions import ImproperlyConfigured

from next.conf.defaults import DEFAULTS
from next.conf.loader import SHAPE, loader_settings, read_settings
from next.conf.shapes import Shape
from next.testing import override_next_settings


class TestReadSettings:
    """`read_settings` answers both keys, the default for an omitted one."""

    def test_the_defaults(self) -> None:
        current = read_settings({})
        assert (current.window, current.max_batch) == (0.001, 500)

    def test_the_shape_covers_every_default_key(self) -> None:
        assert set(SHAPE.keys) == set(DEFAULTS["LOADER"])

    def test_written_values_are_read(self) -> None:
        current = read_settings({"WINDOW": "5ms", "MAX_BATCH": 7})
        assert (current.window, current.max_batch) == (0.005, 7)

    def test_a_misfit_raises_naming_every_value(self) -> None:
        with pytest.raises(ImproperlyConfigured) as raised:
            read_settings({"WINDOW": -1, "MAX_BATCH": 0})
        message = str(raised.value)
        assert "NEXT_FRAMEWORK['LOADER']['WINDOW'] must be" in message
        assert "NEXT_FRAMEWORK['LOADER']['MAX_BATCH'] must be" in message


@pytest.mark.parametrize(
    ("key", "value", "usable"),
    [
        ("WINDOW", 0, True),
        ("WINDOW", "2ms", True),
        ("WINDOW", -1, False),
        ("WINDOW", "soon", False),
        ("MAX_BATCH", 1, True),
        ("MAX_BATCH", 0, False),
        ("MAX_BATCH", True, False),
    ],
)
def test_shapes(key: str, value: object, usable) -> None:
    shape = SHAPE.keys[key]
    assert isinstance(shape, Shape)
    assert shape.usable(value) is usable


def test_the_read_is_held_until_the_mapping_moves() -> None:
    first = loader_settings()
    assert loader_settings() is first
    with override_next_settings(LOADER={"MAX_BATCH": 3}):
        assert loader_settings().max_batch == 3
    assert loader_settings().max_batch == 500
