import pytest

import next.checks as checks_package
from next.checks import _LAZY_ATTRIBUTES


_EAGER = frozenset({"NEXT", "register_all", "reset_check_caches"})


class TestPublicSurface:
    """`__all__` and `__dir__` describe the same names, eager plus lazy."""

    def test_all_matches_dir(self) -> None:
        assert checks_package.__dir__() == sorted(checks_package.__all__)

    def test_all_is_sorted_and_unique(self) -> None:
        listed = checks_package.__all__
        assert listed == sorted(listed)
        assert len(listed) == len(set(listed))

    def test_all_is_the_eager_names_plus_the_lazy_map(self) -> None:
        assert set(checks_package.__all__) == _EAGER | set(_LAZY_ATTRIBUTES)

    def test_all_lists_no_private_name(self) -> None:
        assert not [name for name in checks_package.__all__ if name.startswith("_")]

    def test_lazy_map_declares_only_public_names(self) -> None:
        assert not [name for name in _LAZY_ATTRIBUTES if name.startswith("_")]

    def test_lazy_map_names_only_checks_modules(self) -> None:
        assert all(module.endswith(".checks") for module in _LAZY_ATTRIBUTES.values())

    @pytest.mark.parametrize("name", sorted(_LAZY_ATTRIBUTES))
    def test_every_lazy_name_resolves(self, name: str) -> None:
        assert getattr(checks_package, name) is not None

    @pytest.mark.parametrize("name", sorted(_EAGER))
    def test_every_eager_name_resolves(self, name: str) -> None:
        assert getattr(checks_package, name) is not None

    def test_unknown_name_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'nonexistent'"):
            checks_package.__getattr__("nonexistent")


class TestRetiredReExports:
    """Private page helpers no longer resolve off the checks facade."""

    @pytest.mark.parametrize(
        "name",
        ["_has_template_or_djx", "_load_python_module", "_load_python_module_memo"],
    )
    def test_private_page_helper_is_gone(self, name: str) -> None:
        assert not hasattr(checks_package, name)
