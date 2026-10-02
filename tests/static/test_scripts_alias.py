import pytest

from next import csrf
from next.static import runtime, scripts


class TestDeprecatedModule:
    """`next.static.scripts` resolves every name from where it lives now."""

    @pytest.mark.parametrize(
        ("name", "home"),
        [
            ("NEXT_JS_STATIC_PATH", runtime),
            ("NextScriptBuilder", runtime),
            ("csrf_payload", csrf),
            ("csrf_header_name", csrf),
        ],
    )
    def test_a_name_resolves_with_a_warning(self, name, home) -> None:
        with pytest.warns(DeprecationWarning, match=f"import {name} from"):
            value = getattr(scripts, name)
        assert value is getattr(home, name)

    def test_an_unknown_name_raises(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'missing'"):
            scripts.missing  # noqa: B018
