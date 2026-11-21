from pathlib import Path

import pytest

from next.suspense.checks import calls
from next.suspense.checks.calls import (
    check_client_calls,
    check_declared_props,
    check_prop_types,
    deferred_components,
    mints_tokens,
)
from tests.pages.checks.metadata.trees import COMPONENTS
from tests.support.checks import run_check, write_files


_PAGE = "from next import page\n\npage.public()\n"
_LAYOUT = "<html><body>{% template %}</body></html>"
_CARD = (
    'from next.components import component\n\ncomponent.defer("load", props=("sku",))\n'
)
_RULED = _CARD + 'component.permission_required("auth.view_user")\n'
_OBJECT = _CARD + 'component.permission_required("auth.view_user", obj="thing")\n'
_PROPS = (
    "from next.components import component\n\n"
    'component.props("n")\n\n\n'
    '@component.context("title")\n'
    "def title(n: str) -> str:\n"
    "    return n\n"
)
_CODED = (
    "from next.components import component\n\n"
    'component.props("n")\n\n\n'
    "def render() -> str:\n"
    '    return ""\n'
)
_KEYLESS = _PROPS + "\n\n@component.context\ndef extra() -> dict:\n    return {}\n"
_BARE = "from next.components import component\n"
_ALIASED = (
    "from next.components import component as declare\n\n"
    'declare.props("n")\n\n\n'
    "def render() -> str:\n"
    '    return ""\n'
)


def _tree(tmp_path: Path, body: str, **files: str) -> Path:
    root = tmp_path / "pages"
    write_files(
        root,
        {
            "layout.djx": _LAYOUT,
            "home/page.py": _PAGE,
            "home/template.djx": body,
            "_components/card/component.py": _CARD,
            "_components/card/component.djx": "<p>{{ sku }}</p>",
            "_components/plain/component.djx": "<p>plain</p>",
            **files,
        },
    )
    return root


def _ids(tmp_path: Path, body: str, **files: str) -> list[str]:
    root = _tree(tmp_path, body, **files)
    messages = run_check(check_client_calls, root, COMPONENT_BACKENDS=COMPONENTS)
    return sorted(message.id for message in messages)


class TestShape:
    """`next.E224` refuses undeclared props, children and slots on a client trip."""

    def test_a_declared_call_is_silent(self, tmp_path: Path) -> None:
        assert _ids(tmp_path, '{% component "card" sku="a" %}') == []

    def test_an_undeclared_prop_errors(self, tmp_path: Path) -> None:
        assert _ids(tmp_path, '{% component "card" sku="a" colour="red" %}') == [
            "next.E224"
        ]

    def test_children_error(self, tmp_path: Path) -> None:
        body = '{% #component "card" sku="a" %}<b>child</b>{% /component %}'
        assert _ids(tmp_path, body) == ["next.E224"]

    def test_whitespace_between_the_tags_is_no_child(self, tmp_path: Path) -> None:
        body = '{% #component "card" sku="a" %}  {% /component %}'
        assert _ids(tmp_path, body) == []

    def test_a_call_site_mode_makes_a_plain_component_a_client_trip(
        self, tmp_path: Path
    ) -> None:
        assert _ids(tmp_path, '{% component "plain" defer="visible" n=1 %}') == [
            "next.E224"
        ]

    def test_an_unknown_component_and_a_stream_call_are_skipped(
        self, tmp_path: Path
    ) -> None:
        body = '{% component "ghost" %}{% component "plain" defer="stream" n=1 %}'
        assert _ids(tmp_path, body) == []

    def test_a_call_inside_a_component_template_is_read(self, tmp_path: Path) -> None:
        files = {"_components/outer/component.djx": '{% component "card" x=1 %}'}
        assert _ids(tmp_path, '{% component "outer" %}', **files) == ["next.E224"]


class TestMintsTokens:
    """`mints_tokens` answers whether a component of the project can be handed a token."""

    @staticmethod
    def _mints(tmp_path: Path, body: str, **files: str) -> bool:
        root = _tree(
            tmp_path, body, **{"_components/card/component.py": _BARE, **files}
        )
        (minted,) = run_check(
            lambda *args: [mints_tokens()], root, COMPONENT_BACKENDS=COMPONENTS
        )
        return minted

    def test_a_client_trip_declaration_mints(self, tmp_path: Path) -> None:
        files = {"_components/card/component.py": _CARD}
        assert self._mints(tmp_path, "<p></p>", **files) is True

    def test_declared_props_mint_a_retry_token(self, tmp_path: Path) -> None:
        files = {"_components/titled/component.py": _PROPS}
        assert self._mints(tmp_path, "<p></p>", **files) is True

    def test_a_call_site_client_trip_mints(self, tmp_path: Path) -> None:
        assert self._mints(tmp_path, '{% component "plain" defer="load" %}') is True

    def test_a_project_without_either_mints_nothing(self, tmp_path: Path) -> None:
        assert self._mints(tmp_path, '{% component "plain" %}') is False


class TestUnreadable:
    def test_a_broken_or_missing_component_template_is_skipped(
        self, tmp_path: Path
    ) -> None:
        files = {
            "_components/broken/component.djx": "{% if %}",
            "_components/coded/component.py": _CODED,
            "_components/aliased/component.py": _ALIASED,
        }
        assert _ids(tmp_path, '{% component "card" sku="a" %}', **files) == []


class TestObject:
    """`next.E225` refuses a client-trip rule whose object is no declared prop."""

    def test_an_object_outside_the_props_errors(self, tmp_path: Path) -> None:
        files = {"_components/card/component.py": _OBJECT}
        assert _ids(tmp_path, "<p></p>", **files) == ["next.E225"]

    def test_a_url_row_bound_by_the_page_api_errors(self, tmp_path: Path) -> None:
        files = {
            "_components/card/component.py": _CARD
            + "from django.contrib.auth.models import User\n\n"
            + "from next.deps import DObject\n"
            + "from next.pages import page\n\n"
            + 'page.permission_required("auth.view_user", obj=DObject[User])\n'
        }
        root = _tree(tmp_path, "<p></p>", **files)
        messages = run_check(check_client_calls, root, COMPONENT_BACKENDS=COMPONENTS)
        assert [message.id for message in messages] == ["next.E225"]
        assert "'auth.User'" in messages[0].msg

    def test_an_object_among_the_props_is_silent(self, tmp_path: Path) -> None:
        files = {
            "_components/card/component.py": _CARD.replace(
                '("sku",)', '("sku", "thing")'
            )
            + 'component.permission_required("auth.view_user", obj="thing")\n'
        }
        assert _ids(tmp_path, "<p></p>", **files) == []


class TestUnguarded:
    """`next.W159` warns about a guarded client trip without a rule of its own."""

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(
                '{% if a %}{% component "card" sku="a" %}{% endif %}', id="if"
            ),
            pytest.param(
                '{% has_perm "auth.view_user" row %}{% component "card" sku="a" %}'
                "{% endhas_perm %}",
                id="object-has_perm",
            ),
            pytest.param(
                '{% has_perm name %}{% component "card" sku="a" %}{% endhas_perm %}',
                id="variable-has_perm",
            ),
            pytest.param(
                '{% has_perm "auth.view_user" %}{% else %}'
                '{% component "card" sku="a" %}{% endhas_perm %}',
                id="else-has_perm",
            ),
            pytest.param(
                '{% #component "keeper" %}{% component "card" sku="a" %}'
                "{% /component %}",
                id="guarding-parent",
            ),
        ],
    )
    def test_a_guarded_call_without_a_rule_warns(
        self, tmp_path: Path, body: str
    ) -> None:
        files = {
            "_components/keeper/component.py": (
                "from next.components import component\n\n"
                'component.permission_required("auth.view_user")\n'
            ),
            "_components/keeper/component.djx": "{{ children }}",
        }
        assert "next.W159" in _ids(tmp_path, body, **files)

    def test_a_guarded_call_with_a_rule_is_silent(self, tmp_path: Path) -> None:
        files = {"_components/card/component.py": _RULED}
        body = '{% if a %}{% component "card" sku="a" %}{% endif %}'
        assert _ids(tmp_path, body, **files) == []

    def test_a_model_has_perm_the_reload_checks_again_is_silent(
        self, tmp_path: Path
    ) -> None:
        body = (
            '{% has_perm "auth.view_user" %}{% component "card" sku="a" %}'
            "{% endhas_perm %}"
        )
        assert _ids(tmp_path, body) == []

    def test_a_loop_hides_nothing(self, tmp_path: Path) -> None:
        body = '{% for i in rows %}{% component "card" sku="a" %}{% endfor %}'
        assert _ids(tmp_path, body) == []

    def test_a_plain_parent_guards_nothing(self, tmp_path: Path) -> None:
        files = {"_components/box/component.djx": "{{ children }}"}
        body = '{% #component "box" %}{% component "card" sku="a" %}{% /component %}'
        assert _ids(tmp_path, body, **files) == []


class TestDeclaredProps:
    """`next.W156` warns about a variable a props component is never given."""

    def _ids(self, tmp_path: Path, module: str, template: str) -> list[str]:
        root = _tree(
            tmp_path,
            "<p></p>",
            **{
                "_components/box/component.py": module,
                "_components/box/component.djx": template,
            },
        )
        messages = run_check(check_declared_props, root, COMPONENT_BACKENDS=COMPONENTS)
        return sorted(message.id for message in messages if "'box'" in message.msg)

    def test_a_stray_variable_warns(self, tmp_path: Path) -> None:
        assert self._ids(tmp_path, _PROPS, "{{ n }}{{ title }}{{ stray }}") == [
            "next.W156"
        ]

    def test_given_variables_are_silent(self, tmp_path: Path) -> None:
        template = (
            "{{ n }}{{ title }}{{ request.path }}{{ csrf_token }}"
            "{% for i in title %}{{ i }}{% endfor %}{{ children }}{{ slot_x }}"
        )
        assert self._ids(tmp_path, _PROPS, template) == []

    def test_a_keyless_context_function_is_skipped(self, tmp_path: Path) -> None:
        assert self._ids(tmp_path, _KEYLESS, "{{ stray }}") == []

    def test_a_component_without_a_template_is_skipped(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "<p></p>", **{"_components/coded/component.py": _CODED})
        messages = run_check(check_declared_props, root, COMPONENT_BACKENDS=COMPONENTS)
        assert messages == []

    def test_a_component_with_no_template_file_is_skipped(self, tmp_path: Path) -> None:
        files = {"_components/aliased/component.py": _ALIASED}
        root = _tree(tmp_path, "<p></p>", **files)
        messages = run_check(check_declared_props, root, COMPONENT_BACKENDS=COMPONENTS)
        assert messages == []

    def test_an_unreadable_template_is_skipped(self, tmp_path: Path) -> None:
        assert self._ids(tmp_path, _PROPS, "{% if %}") == []

    def test_a_processor_that_refuses_a_bare_request_skips_the_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(calls, "processor_names", lambda: None)
        assert self._ids(tmp_path, _PROPS, "{{ stray }}") == []


class TestProcessorNames:
    def test_the_names_of_every_processor_are_read(self) -> None:
        names = calls.processor_names()
        assert names is not None
        assert "request" in names

    def test_a_refusing_processor_answers_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refusing(request: object) -> dict[str, object]:
            raise AttributeError

        monkeypatch.setattr(calls, "context_processors", lambda: [refusing])
        assert calls.processor_names() is None


def test_the_deferring_components_are_those_with_a_mode_or_a_loading_ui(
    tmp_path: Path,
) -> None:
    root = _tree(
        tmp_path,
        "<p>home</p>",
        **{
            "_components/slow/component.djx": "<p>slow</p>",
            "_components/slow/loading.djx": "<p>wait</p>",
            "_components/broken/component.djx": "<p>broken</p>",
            "_components/broken/error.djx": "<p>error</p>",
            "_components/sized/component.py": _PROPS,
            "_components/sized/component.djx": "<p>{{ title }}</p>",
        },
    )
    (names,) = run_check(
        lambda *args: [sorted(info.name for info in deferred_components())],
        root,
        COMPONENT_BACKENDS=COMPONENTS,
    )
    assert names == ["card", "slow"]


def test_a_project_without_component_backends_defers_no_component(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, '{% component "card" sku="a" %}')
    (names,) = run_check(
        lambda *args: [list(deferred_components())], root, COMPONENT_BACKENDS=[]
    )
    assert names == []


_TYPED = (
    "from datetime import date\n\n"
    "from next.components import component\n\n"
    'component.defer("load", props=("day", "count"))\n\n\n'
    "@component.context\n"
    "def shown(day: date, count: int) -> dict:\n"
    "    return {}\n"
)
_UNION = _TYPED.replace("count: int", "count: int | str")


def _typed_messages(tmp_path: Path, body: str, module: str = _TYPED) -> list:
    root = _tree(tmp_path, body, **{"_components/card/component.py": module})
    return run_check(check_prop_types, root, COMPONENT_BACKENDS=COMPONENTS)


class TestPropTypes:
    """`next.E243` refuses a declared prop that no boundary token carries."""

    def test_constants_of_the_annotated_types_are_silent(self, tmp_path: Path) -> None:
        body = (
            '{% component "card" count=2 %}{% component "card" day=when %}'
            '{% component "card" count=n|add:1 %}{% component "plain" %}'
        )
        assert _typed_messages(tmp_path, body) == []

    @pytest.mark.parametrize(
        ("body", "named"),
        [
            ('{% component "card" count="2" %}', "holds a str, not a int"),
            ('{% component "card" day="2026-01-02" %}', "holds a str, not a date"),
            ('{% component "card" count=None %}', "holds a NoneType, not a int"),
        ],
    )
    def test_a_constant_of_another_type_errors(
        self, tmp_path: Path, body: str, named: str
    ) -> None:
        (message,) = _typed_messages(tmp_path, body)
        assert message.id == "next.E243"
        assert message.msg.startswith("The call of component 'card' in ")
        assert named in message.msg

    def test_an_unannotated_constant_travels_as_its_own_type(
        self, tmp_path: Path
    ) -> None:
        body = '{% component "card" sku=False %}{% component "card" sku=1.5 %}'
        assert _typed_messages(tmp_path, body, _CARD) == []

    def test_an_annotation_no_token_carries_errors_once(self, tmp_path: Path) -> None:
        body = '{% component "card" count=1 %}{% component "card" count=2 %}'
        (message,) = _typed_messages(tmp_path, body, _UNION)
        assert message.id == "next.E243"
        assert message.msg.startswith(
            "Component 'card' cannot mint its token, as the prop 'count' is annotated"
        )
