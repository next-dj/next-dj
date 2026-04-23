import pytest

from next.testing import build_form_for, resolve_action_url
from tests.forms.actions import SimpleForm


class TestResolveActionUrl:
    """resolve_action_url delegates to the global manager."""

    def test_returns_url_for_registered_action(self) -> None:
        url = resolve_action_url("test_submit")
        assert "_next/form/" in url

    def test_raises_for_unknown_action(self) -> None:
        with pytest.raises(KeyError, match="Unknown form action"):
            resolve_action_url("nonexistent_zz")


class TestBuildFormFor:
    """build_form_for instantiates the form class stored for an action."""

    def test_returns_form_with_data(self) -> None:
        form = build_form_for("test_submit", {"name": "Bob", "email": ""})
        assert isinstance(form, SimpleForm)
        assert form.is_bound
        assert form.is_valid()

    def test_raises_for_unknown_action(self) -> None:
        with pytest.raises(KeyError, match="Unknown form action"):
            build_form_for("nonexistent_zz")

    def test_raises_when_action_has_no_form_class(self) -> None:
        with pytest.raises(LookupError, match="no form_class"):
            build_form_for("test_no_form")
