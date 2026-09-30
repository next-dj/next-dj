import pytest
from django.test import override_settings

from next.conf.defaults import DEFAULTS
from next.conf.frozen import FrozenList
from next.conf.scopes import scope_value, settings_scope
from next.testing import override_next_settings


class TestSettingsScope:
    """A nested scope reads as the merged mapping the user or the defaults hold."""

    def test_an_unset_scope_is_the_default(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert settings_scope("SITE") == DEFAULTS["SITE"]

    def test_a_user_scope_replaces_the_default_whole(self) -> None:
        with override_next_settings(SITE={"NAME": "Acme"}):
            assert settings_scope("SITE") == {"NAME": "Acme"}

    def test_a_key_holding_no_mapping_reads_as_empty(self) -> None:
        assert settings_scope("STRICT_CONTEXT") == {}


class TestScopeValue:
    """A key the user scope leaves out reads the `DEFAULTS` value of that key."""

    def test_a_user_key_wins(self) -> None:
        with override_next_settings(SITE={"NAME": "Acme"}):
            assert scope_value("SITE", "NAME") == "Acme"

    def test_a_missing_key_reads_the_default(self) -> None:
        with override_next_settings(METADATA={"RENDERER": "x.Y"}):
            assert scope_value("METADATA", "CANONICAL_QUERY") == []

    def test_a_default_list_comes_back_frozen(self) -> None:
        with override_next_settings(CONSENT={"SERVER_RENDER": True}):
            categories = scope_value("CONSENT", "CATEGORIES")
        assert isinstance(categories, FrozenList)
        with pytest.raises(TypeError):
            categories.append("x")

    def test_a_key_no_default_names_reads_none(self) -> None:
        assert scope_value("SITE", "UNKNOWN") is None
