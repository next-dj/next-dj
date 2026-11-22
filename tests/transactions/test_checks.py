from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
from django.db import connections
from django.test import override_settings

from next.conf.checks import check_next_framework_value_types
from next.conf.defaults import DEFAULTS
from next.forms.registration import registration_diagnostics
from next.pages import declarations as page_declarations
from next.pages.loaders import load_python_module_memo
from next.pages.transactions import atomic_store
from next.transactions.checks import (
    check_atomic_declarations,
    check_atomic_requests,
    check_transactions_aliases,
    check_transactions_settings,
)


def _transactions(**scope: object) -> override_settings:
    return override_settings(NEXT_FRAMEWORK={"TRANSACTIONS": scope})


class TestTransactionsSettings:
    """`check_transactions_settings` reports keys, value shapes and the rule path."""

    def test_silent_without_the_scope(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_transactions_settings() == []

    def test_a_non_dict_scope_is_left_to_the_type_check(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"TRANSACTIONS": "on"}):
            assert check_transactions_settings() == []
            assert [m.id for m in check_next_framework_value_types()] == ["next.E076"]

    @pytest.mark.parametrize("aliases", ["all", ["default"], ("default",)])
    def test_silent_on_a_valid_scope(self, aliases: object) -> None:
        with _transactions(
            SAFE=True, UNSAFE=False, ALIASES=aliases, RULE="django.utils.timezone.now"
        ):
            assert check_transactions_settings() == []

    def test_silent_on_the_writes_mode(self) -> None:
        with _transactions(SAFE="writes", UNSAFE="writes"):
            assert check_transactions_settings() == []

    def test_an_unknown_key_yields_e035(self) -> None:
        with _transactions(SAFE=False, ATOMIC=True):
            (message,) = check_transactions_settings()
        assert message.id == "next.E035"
        assert message.msg.startswith("NEXT_FRAMEWORK['TRANSACTIONS'] has unknown keys")
        assert "'ATOMIC'" in message.msg
        allowed = ", ".join(sorted(DEFAULTS["TRANSACTIONS"]))
        assert message.msg.endswith(f"Allowed keys are {allowed}.")

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("SAFE", "yes"),
            ("UNSAFE", 1),
            ("ALIASES", "default"),
            ("ALIASES", ["default", 1]),
            ("RULE", 42),
        ],
        ids=["safe_str", "unsafe_int", "aliases_str", "aliases_mixed", "rule_int"],
    )
    def test_a_mistyped_value_yields_e076(self, key: str, value: object) -> None:
        with _transactions(**{key: value}):
            (message,) = check_transactions_settings()
        assert message.id == "next.E076"
        assert message.msg.startswith(
            f"NEXT_FRAMEWORK['TRANSACTIONS'][{key!r}] must be"
        )

    def test_the_mistyped_value_message_text(self) -> None:
        with _transactions(SAFE="yes"):
            (message,) = check_transactions_settings()
        assert message.msg == (
            "NEXT_FRAMEWORK['TRANSACTIONS']['SAFE'] must be True, False or 'writes', "
            "got 'yes'. The "
            "framework ignores this value and keeps its default."
        )
        assert message.hint == (
            "Write the value in the shape named, or drop the key for its default."
        )

    @pytest.mark.parametrize(
        ("rule", "said"),
        [
            ("tests.missing.rule", "does not import ("),
            ("tests.support.broken.rule", "raised RuntimeError"),
            (
                "next.conf.defaults.USER_SETTING",
                (
                    "names no callable, so the transaction rule raises "
                    "ImproperlyConfigured for every request it is asked about."
                ),
            ),
        ],
        ids=["missing", "raising", "not_callable"],
    )
    def test_an_unusable_rule_yields_e219(self, rule: str, said: str) -> None:
        with _transactions(RULE=rule):
            (message,) = check_transactions_settings()
        assert message.id == "next.E219"
        assert message.msg.startswith(
            f"NEXT_FRAMEWORK['TRANSACTIONS']['RULE'] {rule!r} "
        )
        assert said in message.msg


class TestTransactionsAliases:
    """`check_transactions_aliases` warns about aliases Django holds no connection for."""

    @pytest.mark.parametrize(
        "next_framework",
        [
            {},
            {"TRANSACTIONS": "on"},
            {"TRANSACTIONS": {"ALIASES": "all"}},
            {"TRANSACTIONS": {"ALIASES": ["default"]}},
            {"TRANSACTIONS": {"ALIASES": ["default", 1]}},
        ],
        ids=["no_scope", "no_dict", "all", "declared", "mistyped"],
    )
    def test_silent(self, next_framework: dict[str, object]) -> None:
        with override_settings(NEXT_FRAMEWORK=next_framework):
            assert check_transactions_aliases() == []

    def test_a_missing_alias_yields_w179_once(self) -> None:
        with _transactions(ALIASES=["default", "replica", "replica"]):
            (message,) = check_transactions_aliases()
        assert message.id == "next.W179"
        assert message.msg == (
            "NEXT_FRAMEWORK['TRANSACTIONS']['ALIASES'] names 'replica', which "
            "DATABASES does not declare, so next.dj views open no transaction on it."
        )
        assert message.hint == "Add the alias to DATABASES or remove it from ALIASES."

    def test_a_second_connection_is_declared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(connections, "settings", {"default": {}, "replica": {}})
        with _transactions(ALIASES=["default", "replica"]):
            assert check_transactions_aliases() == []


def _atomic_page(directory: Path, declaration: str, name: str = "page.py") -> Path:
    page_file = directory / name
    page_file.write_text(f"from next.pages import page\n\n{declaration}\n")
    load_python_module_memo(page_file)
    return page_file


class TestAtomicDeclarations:
    """`check_atomic_declarations` reports a transaction choice that applies nothing."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setattr(registration_diagnostics, "invalid_atomic", [])
        atomic_store.reset()
        yield
        atomic_store.reset()

    @pytest.mark.parametrize(
        "call", ["page.atomic(True, aliases=['default'])", "page.atomic('writes')"]
    )
    def test_silent_on_usable_declarations(self, tmp_path: Path, call: str) -> None:
        _atomic_page(tmp_path, call)
        assert check_atomic_declarations() == []

    def test_an_unusable_page_declaration_yields_e240(self, tmp_path: Path) -> None:
        page_file = _atomic_page(tmp_path, "page.atomic('yes')")
        (message,) = check_atomic_declarations()
        assert message.id == "next.E240"
        assert message.msg == (
            f"page.atomic() in {page_file} declares no transaction, since atomic "
            "must be True, False or 'writes', got 'yes'. The page follows the method "
            "rule instead."
        )

    def test_a_call_inside_a_function_yields_e240(self, tmp_path: Path) -> None:
        _atomic_page(tmp_path, "def declare():\n    page.atomic(True)\n\n\ndeclare()")
        (message,) = check_atomic_declarations()
        assert message.id == "next.E240"
        assert "inside a function" in message.msg

    @pytest.mark.parametrize("name", ["component.py", "helpers.py"])
    def test_a_call_outside_a_page_module_yields_e240_unloaded(
        self, tmp_path: Path, name: str
    ) -> None:
        source = _atomic_page(tmp_path, "page.atomic(True)", name=name)
        with mock.patch.object(page_declarations, "load_page_module") as load:
            (message,) = check_atomic_declarations()
        load.assert_not_called()
        assert message.id == "next.E240"
        assert message.msg == (
            f"page.atomic() in {source} declares no transaction, since the call sits "
            "outside a page.py and so names no page."
        )

    def test_an_unusable_action_choice_yields_e240(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registration_diagnostics,
            "invalid_atomic",
            [("@action 'vote'", "atomic must be True, False or 'writes', got 1")],
        )
        (message,) = check_atomic_declarations()
        assert message.id == "next.E240"
        assert message.msg == (
            "@action 'vote' declares no transaction, since atomic must be True, "
            "False or 'writes', got 1. The action follows the method rule instead."
        )


class TestAtomicRequests:
    """`check_atomic_requests` warns that next.dj views follow `TRANSACTIONS`."""

    def test_silent_without_atomic_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(connections.settings["default"], "ATOMIC_REQUESTS", False)
        assert check_atomic_requests() == []

    def test_atomic_requests_yields_w154_naming_the_aliases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            connections,
            "settings",
            {
                "default": {"ATOMIC_REQUESTS": True},
                "replica": {"ATOMIC_REQUESTS": False},
                "audit": {"ATOMIC_REQUESTS": True},
                "bare": {},
            },
        )
        (message,) = check_atomic_requests()
        assert message.id == "next.W154"
        assert message.msg == (
            "ATOMIC_REQUESTS is set on 'default', 'audit'. next.dj views ignore it "
            "and open the transactions NEXT_FRAMEWORK['TRANSACTIONS'] asks for, "
            "while other Django views keep it."
        )
