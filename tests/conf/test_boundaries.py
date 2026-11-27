import pytest
from django.core.exceptions import ImproperlyConfigured

from next.conf.boundaries import SHAPE, boundary_settings, read_settings
from next.conf.defaults import DEFAULTS
from next.conf.shapes import Misfit, Shape, misfits
from next.testing import override_next_settings


class TestReadSettings:
    """`read_settings` answers every key, the default for an omitted one."""

    def test_the_defaults(self) -> None:
        current = read_settings({})
        assert (current.loading_template, current.error_template) == (None, None)
        assert (current.reraise, current.reporter) == (False, None)
        assert current.atomic == "writes"
        assert (current.opaque, current.detail) == (False, True)

    def test_the_shape_covers_every_default_key(self) -> None:
        assert set(SHAPE.keys) == set(DEFAULTS["BOUNDARIES"])

    def test_written_values_are_read(self) -> None:
        current = read_settings(
            {
                "LOADING_TEMPLATE": "loading.html",
                "ERROR_TEMPLATE": "error.html",
                "RAISE": True,
                "REPORTER": "myapp.report",
                "ATOMIC": False,
                "KINDS": "opaque",
                "DEBUG_DETAIL": False,
            }
        )
        assert (current.loading_template, current.error_template) == (
            "loading.html",
            "error.html",
        )
        assert (current.reraise, current.reporter) == (True, "myapp.report")
        assert current.atomic is False
        assert (current.opaque, current.detail) == (True, False)

    def test_a_misfit_raises_naming_every_value(self) -> None:
        with pytest.raises(ImproperlyConfigured) as raised:
            read_settings({"KINDS": "brief", "ATOMIC": "reads"})
        message = str(raised.value)
        assert "NEXT_FRAMEWORK['BOUNDARIES']['KINDS'] must be" in message
        assert "NEXT_FRAMEWORK['BOUNDARIES']['ATOMIC'] must be" in message


@pytest.mark.parametrize(
    ("key", "value", "usable"),
    [
        ("LOADING_TEMPLATE", 3, False),
        ("LOADING_TEMPLATE", "skeleton.djx", True),
        ("ERROR_TEMPLATE", "", False),
        ("RAISE", 1, False),
        ("REPORTER", "", False),
        ("REPORTER", "a.b", True),
        ("ATOMIC", 1, False),
        ("ATOMIC", True, True),
        ("KINDS", "detailed", True),
        ("DEBUG_DETAIL", None, False),
    ],
)
def test_shapes(key: str, value: object, usable) -> None:
    shape = SHAPE.keys[key]
    assert isinstance(shape, Shape)
    assert shape.usable(value) is usable


def test_misfits_name_the_key_and_the_value() -> None:
    assert misfits({"KINDS": "brief", "OLD": 1}, SHAPE) == [
        Misfit((), unknown=("OLD",), allowed=tuple(sorted(DEFAULTS["BOUNDARIES"]))),
        Misfit(("KINDS",), "'detailed' or 'opaque'", "'brief'"),
    ]


class TestBoundarySettings:
    """`boundary_settings` reads the merged mapping again once it moves."""

    def test_the_read_is_held_until_the_mapping_moves(self) -> None:
        first = boundary_settings()
        assert boundary_settings() is first
        with override_next_settings(BOUNDARIES={"RAISE": True}):
            assert boundary_settings().reraise is True
        assert boundary_settings().reraise is False
