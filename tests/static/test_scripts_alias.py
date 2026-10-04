import pytest
from django.test import RequestFactory

from next import csrf
from next.static import runtime, scripts


class TestDeprecatedModule:
    """`next.static.scripts` resolves every name from the module that defines it."""

    @pytest.mark.parametrize(
        ("name", "home", "current"),
        [
            ("NEXT_JS_STATIC_PATH", runtime, "NEXT_JS_STATIC_PATH"),
            ("NextScriptBuilder", runtime, "NextScriptBuilder"),
            ("csrf_payload", csrf, "csrf_token_payload"),
            ("csrf_header_name", csrf, "csrf_header_name"),
        ],
    )
    def test_a_name_resolves_with_a_warning(self, name, home, current) -> None:
        with pytest.warns(DeprecationWarning, match=f"import {current} from"):
            value = getattr(scripts, name)
        assert value is getattr(home, current)

    def test_the_deprecated_csrf_payload_carries_a_token(self) -> None:
        """The deprecated name returns the CSRF payload, on a deferred render too."""
        request = RequestFactory().get("/")
        setattr(request, csrf.CSRF_DEFERRED_ATTR, True)
        with pytest.warns(DeprecationWarning, match="import csrf_token_payload"):
            payload = scripts.csrf_payload(request)
        assert set(payload) == {"header", "token"}

    def test_an_unknown_name_raises(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'missing'"):
            scripts.missing  # noqa: B018
