from types import SimpleNamespace

import pytest
from django.test import override_settings

from next.conf import scopes
from next.conf.defaults import DEFAULTS
from next.conf.frozen import FrozenList
from next.conf.scopes import scope_value
from next.testing import override_next_settings


class TestScopeValue:
    """A key the user scope leaves out reads the `DEFAULTS` value of that key."""

    def test_an_unset_scope_reads_the_defaults(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert scope_value("SITE", "INDEXABLE") == DEFAULTS["SITE"]["INDEXABLE"]

    def test_a_scope_holding_no_mapping_reads_the_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scopes, "next_framework_settings", SimpleNamespace(SITE=7))
        assert scope_value("SITE", "INDEXABLE") == DEFAULTS["SITE"]["INDEXABLE"]

    def test_a_default_is_frozen_once(self) -> None:
        """Two reads of one omitted key answer the same frozen value, not two copies."""
        with override_next_settings(CONSENT={"SERVER_RENDER": True}):
            first = scope_value("CONSENT", "CATEGORIES")
            second = scope_value("CONSENT", "CATEGORIES")
        assert first is second

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
