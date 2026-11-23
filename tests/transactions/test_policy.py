import functools
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.base import BaseHandler
from django.db import connection, connections, transaction
from django.db.utils import ConnectionHandler
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings
from django.utils.module_loading import import_string

from next.transactions import (
    SAFE_METHODS,
    WRITES,
    AtomicOverride,
    AtomicScope,
    ViewKind,
    atomic_scope,
    override_of,
    override_problem,
    policy,
    probe_atomic_aliases,
    request_atomic,
    safe_aliases,
    skip_atomic_requests,
)


RULE_PATH = "tests.transactions.test_policy.rule_by_kind"
TWO_ALIASES = ("default", "replica")
SQLITE = "django.db.backends.sqlite3"
EAGER = AtomicScope(("default",), atomic=True)
LAZY = AtomicScope(("default",), atomic=WRITES)

not_callable = 42


def rule_by_kind(request: HttpRequest, kind: ViewKind) -> tuple[str, ...]:
    return ("default",) if kind is ViewKind.ACTION else ()


def rule_one_name(request: HttpRequest, kind: ViewKind) -> str:
    return "default"


def rule_with_unknown(request: HttpRequest, kind: ViewKind) -> list[str]:
    return ["replica", "default", "default"]


def rule_lazy_actions(request: HttpRequest, kind: ViewKind) -> AtomicOverride:
    return AtomicOverride(atomic=WRITES if kind is ViewKind.ACTION else False)


def rule_misfit_override(request: HttpRequest, kind: ViewKind) -> AtomicOverride:
    return AtomicOverride(atomic="reads")


def _request(method: str) -> HttpRequest:
    return RequestFactory().generic(method, "/")


def _transactions(**scope: object) -> override_settings:
    return override_settings(NEXT_FRAMEWORK={"TRANSACTIONS": scope})


def _connections(
    monkeypatch: pytest.MonkeyPatch,
    aliases: tuple[str, ...] = TWO_ALIASES,
    *,
    atomic_requests: bool = False,
) -> None:
    """Give Django's connection handler these aliases, as a second database would."""
    declared = {
        alias: {"ENGINE": SQLITE, "ATOMIC_REQUESTS": atomic_requests}
        for alias in aliases
    }
    monkeypatch.setattr(connections, "settings", declared)


def _create_then_fail(name: str) -> None:
    with request_atomic(EAGER):
        Group.objects.create(name=name)
        msg = "view failed"
        raise ValueError(msg)


def _hook_then_fail(committed: list[str]) -> None:
    with request_atomic(EAGER):
        transaction.on_commit(lambda: committed.append("hook"))
        msg = "view failed"
        raise ValueError(msg)


class TestViewKind:
    def test_every_view_names_itself(self) -> None:
        assert [kind.value for kind in ViewKind] == [
            "page",
            "action",
            "zone",
            "boundary",
            "navigation",
        ]


class TestMethodTable:
    """Safe methods follow `SAFE`, every other method follows `UNSAFE`."""

    def test_safe_methods(self) -> None:
        assert frozenset({"GET", "HEAD", "OPTIONS", "TRACE"}) == SAFE_METHODS

    @pytest.mark.parametrize("method", sorted(SAFE_METHODS))
    @pytest.mark.parametrize("kind", list(ViewKind))
    def test_a_safe_method_opens_nothing_by_default(
        self, method: str, kind: ViewKind
    ) -> None:
        assert atomic_scope(_request(method), kind).aliases == ()

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    @pytest.mark.parametrize("kind", list(ViewKind))
    def test_an_unsafe_method_wraps_the_default_alias(
        self, method: str, kind: ViewKind
    ) -> None:
        assert atomic_scope(_request(method), kind).aliases == ("default",)

    def test_safe_turned_on_wraps_a_get(self) -> None:
        with _transactions(SAFE=True):
            assert atomic_scope(_request("GET"), ViewKind.PAGE).aliases == ("default",)

    def test_unsafe_turned_off_leaves_a_post_bare(self) -> None:
        with _transactions(UNSAFE=False):
            assert atomic_scope(_request("POST"), ViewKind.ACTION).aliases == ()

    def test_an_eager_choice_opens_on_entry(self) -> None:
        assert atomic_scope(_request("POST"), ViewKind.PAGE) == EAGER

    @pytest.mark.parametrize(("key", "method"), [("SAFE", "GET"), ("UNSAFE", "POST")])
    def test_writes_opens_before_the_first_write(self, key: str, method: str) -> None:
        with _transactions(**{key: WRITES}):
            assert atomic_scope(_request(method), ViewKind.PAGE) == LAZY

    @pytest.mark.parametrize(("key", "value"), [("SAFE", "yes"), ("UNSAFE", 0)])
    def test_a_non_bool_flag_keeps_its_default(self, key: str, value: object) -> None:
        with _transactions(**{key: value}):
            assert atomic_scope(_request("GET"), ViewKind.PAGE).aliases == ()
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)


class TestAliases:
    """`ALIASES` names the databases to wrap, matched against Django's connections."""

    def test_all_names_every_alias_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch)
        with _transactions(ALIASES="all"):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == TWO_ALIASES

    def test_a_list_keeps_its_order_without_repeats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch)
        with _transactions(ALIASES=["replica", "default", "replica"]):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == (
                "replica",
                "default",
            )

    def test_an_alias_without_a_connection_is_skipped(self) -> None:
        with _transactions(ALIASES=["replica", "default"]):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)

    @pytest.mark.parametrize("value", ["default", ["default", 1], 42])
    def test_an_unusable_value_keeps_the_default(self, value: object) -> None:
        with _transactions(ALIASES=value):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)

    def test_no_connection_means_no_transaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, ())
        assert atomic_scope(_request("POST"), ViewKind.ACTION).aliases == ()

    def test_a_project_without_databases_opens_no_transaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(policy, "connections", ConnectionHandler({}))
        assert atomic_scope(_request("POST"), ViewKind.ACTION).aliases == ()
        with _transactions(ALIASES="all"):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ()

    def test_a_dummy_alias_is_skipped_beside_a_real_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        declared = {
            "default": {"ENGINE": SQLITE},
            "replica": {"ENGINE": "django.db.backends.dummy"},
        }
        monkeypatch.setattr(connections, "settings", declared)
        with _transactions(ALIASES="all"):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)

    def test_new_connection_settings_are_read_afresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _transactions(ALIASES="all"):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)
            _connections(monkeypatch)
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == TWO_ALIASES

    def test_databases_the_connections_never_read_are_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": {}, "replica": {}})
        with _transactions(ALIASES="all"):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)


class TestRule:
    """`RULE` replaces `SAFE`, `UNSAFE` and `ALIASES` when set."""

    def test_the_rule_answers_for_every_method(self) -> None:
        with _transactions(RULE=RULE_PATH, SAFE=True):
            assert atomic_scope(_request("GET"), ViewKind.ACTION).aliases == (
                "default",
            )
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ()

    def test_one_name_is_one_alias(self) -> None:
        with _transactions(RULE="tests.transactions.test_policy.rule_one_name"):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)

    def test_an_answer_is_matched_against_the_connections(self) -> None:
        with _transactions(RULE="tests.transactions.test_policy.rule_with_unknown"):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)

    def test_the_rule_is_imported_once_per_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        imported: list[str] = []

        def counting(dotted: str) -> object:
            imported.append(dotted)
            return import_string(dotted)

        monkeypatch.setattr(policy, "import_string", counting)
        with _transactions(RULE=RULE_PATH):
            atomic_scope(_request("POST"), ViewKind.ACTION)
            atomic_scope(_request("GET"), ViewKind.PAGE)
        assert imported == [RULE_PATH]

    def test_a_settings_reload_drops_the_rule(self) -> None:
        with _transactions(RULE=RULE_PATH):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ()
        assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)

    @pytest.mark.parametrize(
        ("rule", "match"),
        [
            pytest.param("tests.missing.rule", "could not be imported", id="no-import"),
            pytest.param(
                "tests.transactions.test_policy.not_callable",
                "names no callable",
                id="no-callable",
            ),
            pytest.param(
                "tests.transactions.test_policy.rule_misfit_override",
                "got 'reads'",
                id="override-applying-nothing",
            ),
        ],
    )
    def test_an_unusable_rule_raises(self, rule: str, match: str) -> None:
        with _transactions(RULE=rule), pytest.raises(ImproperlyConfigured, match=match):
            atomic_scope(_request("POST"), ViewKind.PAGE)

    def test_an_override_answer_chooses_the_mode(self) -> None:
        with _transactions(RULE="tests.transactions.test_policy.rule_lazy_actions"):
            assert atomic_scope(_request("POST"), ViewKind.ACTION) == LAZY
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ()

    def test_an_alias_answer_opens_on_entry(self) -> None:
        with _transactions(RULE="tests.transactions.test_policy.rule_one_name"):
            assert atomic_scope(_request("GET"), ViewKind.PAGE) == EAGER

    def test_a_rule_that_is_no_path_keeps_the_method_table(self) -> None:
        with _transactions(RULE=42):
            assert atomic_scope(_request("POST"), ViewKind.PAGE).aliases == ("default",)


class TestSafeAliases:
    """What every safe request of a view opens is known before any request."""

    def test_the_default_table_opens_nothing(self) -> None:
        assert safe_aliases() == ()

    def test_safe_turned_on_names_the_default_alias(self) -> None:
        with _transactions(SAFE=True):
            assert safe_aliases() == ("default",)

    def test_a_rule_answers_per_request_so_nothing_is_held(self) -> None:
        with _transactions(RULE=RULE_PATH):
            assert safe_aliases() is None

    def test_an_override_is_known_ahead_even_under_a_rule(self) -> None:
        with _transactions(RULE=RULE_PATH):
            assert safe_aliases(AtomicOverride(atomic=True)) == ("default",)
            assert safe_aliases(AtomicOverride(atomic=False)) == ()

    def test_writes_names_the_aliases_it_will_open(self) -> None:
        with _transactions(SAFE=WRITES):
            assert safe_aliases() == ("default",)
        assert safe_aliases(AtomicOverride(atomic=WRITES)) == ("default",)


class TestOverrideShape:
    @pytest.mark.parametrize(
        ("atomic", "aliases", "expected"),
        [
            (True, None, AtomicOverride(atomic=True)),
            (False, "all", AtomicOverride(atomic=False, aliases="all")),
            (True, ["a", "b"], AtomicOverride(atomic=True, aliases=("a", "b"))),
            (True, ("a",), AtomicOverride(atomic=True, aliases=("a",))),
            (WRITES, None, AtomicOverride(atomic=WRITES)),
        ],
    )
    def test_a_usable_pair_makes_an_override(
        self, atomic: object, aliases: object, expected: AtomicOverride
    ) -> None:
        assert override_problem(atomic, aliases) is None
        assert override_of(atomic, aliases) == expected

    @pytest.mark.parametrize(
        ("atomic", "aliases", "fragment"),
        [
            ("yes", None, "atomic must be True, False or 'writes', got 'yes'"),
            (1, None, "got 1"),
            ("write", None, "got 'write'"),
            (object(), None, "got a object"),
            (True, "default", "aliases must be None, 'all'"),
            (True, ["default", 1], "aliases must be None, 'all'"),
            (True, 42, "got 42"),
        ],
    )
    def test_an_unusable_pair_makes_none(
        self, atomic: object, aliases: object, fragment: str
    ) -> None:
        problem = override_problem(atomic, aliases)
        assert problem is not None
        assert fragment in problem
        assert override_of(atomic, aliases) is None


class TestOverride:
    """A per-view `AtomicOverride` wins over the method table and the rule."""

    def test_off_leaves_an_unsafe_method_bare(self) -> None:
        override = AtomicOverride(atomic=False)
        assert atomic_scope(_request("POST"), ViewKind.ACTION, override).aliases == ()

    def test_on_wraps_a_safe_method_in_the_configured_aliases(self) -> None:
        override = AtomicOverride(atomic=True)
        assert atomic_scope(_request("GET"), ViewKind.PAGE, override).aliases == (
            "default",
        )

    def test_own_aliases_are_matched_against_the_connections(self) -> None:
        override = AtomicOverride(atomic=True, aliases=("replica", "default"))
        assert atomic_scope(_request("GET"), ViewKind.PAGE, override).aliases == (
            "default",
        )

    def test_all_names_every_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _connections(monkeypatch)
        override = AtomicOverride(atomic=True, aliases="all")
        assert (
            atomic_scope(_request("GET"), ViewKind.PAGE, override).aliases
            == TWO_ALIASES
        )

    def test_writes_keeps_the_aliases_and_waits(self) -> None:
        override = AtomicOverride(atomic=WRITES)
        assert atomic_scope(_request("GET"), ViewKind.PAGE, override) == LAZY

    def test_the_override_wins_over_the_rule(self) -> None:
        override = AtomicOverride(atomic=True)
        with _transactions(RULE=RULE_PATH):
            assert atomic_scope(_request("POST"), ViewKind.PAGE, override).aliases == (
                "default",
            )


def _recorder(events: list[str], refused: str | None = None) -> Callable[..., object]:
    @contextmanager
    def atomic(using: str | None = None) -> Generator[None]:
        if using == refused:
            msg = f"cannot open {using}"
            raise RuntimeError(msg)
        events.append(f"enter {using}")
        try:
            yield
        finally:
            events.append(f"exit {using}")

    return atomic


class TestRequestAtomicOrder:
    """The blocks nest as `ATOMIC_REQUESTS` nests them, the last alias outermost."""

    def test_the_last_alias_is_outermost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(events))
        with request_atomic(AtomicScope(TWO_ALIASES, atomic=True)):
            events.append("view")
        assert events == [
            "enter replica",
            "enter default",
            "view",
            "exit default",
            "exit replica",
        ]

    def test_one_alias_opens_one_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(events))
        with request_atomic(EAGER):
            events.append("view")
        assert events == ["enter default", "view", "exit default"]

    def test_one_alias_is_the_django_block_itself(self) -> None:
        assert isinstance(request_atomic(EAGER), transaction.Atomic)

    def test_the_order_matches_atomic_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, atomic_requests=True)
        wrapped: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(wrapped))

        def view(request: HttpRequest) -> HttpResponse:
            wrapped.append("view")
            return HttpResponse()

        BaseHandler().make_view_atomic(view)(_request("POST"))
        opened: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(opened))
        with request_atomic(AtomicScope(tuple(connections), atomic=True)):
            opened.append("view")
        assert opened == wrapped
        assert opened[0] == "enter replica"

    def test_a_block_that_fails_to_open_closes_the_ones_before_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(events, "default"))
        with (
            pytest.raises(RuntimeError, match="cannot open default"),
            request_atomic(AtomicScope(TWO_ALIASES, atomic=True)),
        ):
            events.append("view")
        assert events == ["enter replica", "exit replica"]

    def test_no_alias_touches_no_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[object] = []
        touched: list[object] = []
        monkeypatch.setattr(
            transaction, "atomic", lambda *args, **kwargs: opened.append(kwargs)
        )

        def touch(handler: ConnectionHandler, alias: str) -> None:
            touched.append(alias)

        monkeypatch.setattr(ConnectionHandler, "__getitem__", touch)
        with request_atomic(AtomicScope((), atomic=True)):
            pass
        assert opened == []
        assert touched == []
        assert request_atomic(
            AtomicScope(("default",), atomic=False)
        ) is request_atomic(AtomicScope((), atomic=True))


@pytest.mark.django_db(transaction=True)
class TestRequestAtomicDatabase:
    """The blocks commit on a normal exit and roll back on an exception."""

    def test_a_normal_exit_commits(self) -> None:
        with request_atomic(EAGER):
            Group.objects.create(name="committed")
        assert Group.objects.filter(name="committed").exists()

    def test_an_exception_rolls_back(self) -> None:
        with pytest.raises(ValueError, match="view failed"):
            _create_then_fail("rolled back")
        assert not Group.objects.filter(name="rolled back").exists()

    def test_the_block_opens_before_the_first_query(self) -> None:
        committed: list[str] = []
        with request_atomic(EAGER):
            assert connection.in_atomic_block
            transaction.on_commit(lambda: committed.append("hook"))
            assert committed == []
            Group.objects.create(name="eager")
            assert committed == []
        assert committed == ["hook"]

    def test_a_rollback_drops_the_on_commit_hook(self) -> None:
        committed: list[str] = []
        with pytest.raises(ValueError, match="view failed"):
            _hook_then_fail(committed)
        assert committed == []


class TestSkipAtomicRequests:
    """A marked view stays outside Django's `ATOMIC_REQUESTS` wrapping on every alias."""

    def test_an_unmarked_view_is_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _connections(monkeypatch, atomic_requests=True)

        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        assert BaseHandler().make_view_atomic(view) is not view

    def test_a_marked_view_is_left_alone_on_both_aliases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, atomic_requests=True)

        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        marked = skip_atomic_requests(view)
        assert marked is view
        assert BaseHandler().make_view_atomic(marked) is view

    def test_an_alias_connected_after_the_mark_is_skipped_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        skip_atomic_requests(view)
        _connections(monkeypatch, atomic_requests=True)
        assert BaseHandler().make_view_atomic(view) is view

    def test_a_wraps_wrapper_is_marked_and_its_inner_view_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, atomic_requests=True)

        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        inner = transaction.non_atomic_requests(using="default")(view)

        @functools.wraps(inner)
        def outer(request: HttpRequest) -> HttpResponse:
            return inner(request)

        skip_atomic_requests(outer)
        assert BaseHandler().make_view_atomic(outer) is outer
        assert inner._non_atomic_requests == {"default"}
        assert "replica" not in inner._non_atomic_requests

    def test_django_marking_afterwards_keeps_every_alias(self) -> None:
        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        skip_atomic_requests(view)
        transaction.non_atomic_requests(using="replica")(view)
        assert "replica" in set(view._non_atomic_requests)
        assert "any alias" in view._non_atomic_requests


class TestProbeAtomicAliases:
    """The probe names the aliases inside an atomic block and opens no connection."""

    @pytest.mark.django_db(transaction=True)
    def test_outside_a_block_names_none(self) -> None:
        assert probe_atomic_aliases() == ()

    @pytest.mark.django_db(transaction=True)
    def test_inside_a_block_names_the_alias(self) -> None:
        with transaction.atomic():
            assert probe_atomic_aliases() == ("default",)

    def test_a_thread_without_connections_gets_none_created(self) -> None:
        seen: list[object] = []

        def probe() -> None:
            seen.append(probe_atomic_aliases())
            seen.append(connections.all(initialized_only=True))

        worker = threading.Thread(target=probe)
        worker.start()
        worker.join()
        assert seen == [(), []]
