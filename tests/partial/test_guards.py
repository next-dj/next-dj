from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Engine, Template
from django.template.base import TextNode

from next.components import ComponentInfo, sources
from next.components.renderers import TemplateSource
from next.partial import guards
from next.partial.guards import Components, bound_zones, fresh, guarded_zones
from tests.support import touch_later
from tests.support.guards import (
    CHILDREN_BODY,
    GUARDED_CHILDREN,
    GUARDED_SET_SLOT,
    NESTED_COMPONENT_BODY,
    PERM,
    PLAIN_COMPONENT,
    SECRET_ZONE,
    fill_slot,
    wrap,
)


_GUARD = f'{{% has_perm "{PERM}" %}}'
_END = "{% endhas_perm %}"


class _Loader:
    """Hand back the source a stub manager holds for a component."""

    def __init__(self, sources: dict[str, TemplateSource | None]) -> None:
        self.sources = sources

    def load_source(self, info: ComponentInfo) -> TemplateSource | None:
        return self.sources[info.name]


class _Manager:
    """A components manager over named template texts, `None` for an unreadable one."""

    def __init__(self, root: Path, **components: str | None) -> None:
        self.root = root
        self.sources: dict[str, TemplateSource | None] = {}
        self.template_loader = _Loader(self.sources)
        self.asked: list[str] = []
        self.modules: set[str] = set()
        for name, text in components.items():
            self.write(name, text)

    def write(self, name: str, text: str | None) -> Path:
        path = self.root / f"{name}.djx"
        if text is None:
            self.sources[name] = None
        else:
            path.write_text(text)
            self.sources[name] = TemplateSource(text, path)
        return path

    def get_component(self, name: str, anchor: Path) -> ComponentInfo | None:
        self.asked.append(name)
        if name not in self.sources:
            return None
        module = self.root / name / "component.py" if name in self.modules else None
        return ComponentInfo(
            name=name,
            scope_root=self.root,
            scope_relative="",
            template_path=self.root / f"{name}.djx",
            module_path=module,
            is_simple=module is None,
        )


@pytest.fixture()
def page_file(tmp_path: Path) -> Path:
    path = tmp_path / "page.py"
    path.write_text("x = 1\n")
    return path


def _components(page_file: Path, **sources: str | None) -> Components:
    return Components(_Manager(page_file.parent, **sources), page_file)


def _forget_cached_templates() -> None:
    """Drop what the cached loader holds, so an edited include is read again."""
    Engine.get_default().template_loaders[0].reset()


class TestComponentGuarding:
    """A component guards when its template reaches a check or cannot be followed."""

    @pytest.mark.parametrize(
        ("text", "guarding"),
        [
            pytest.param(GUARDED_CHILDREN, True, id="has-perm"),
            pytest.param(
                f"{_GUARD}x{{% else %}}{{{{ children }}}}{_END}", True, id="else"
            ),
            pytest.param(PLAIN_COMPONENT, False, id="plain"),
            pytest.param("{% has_perm %}", True, id="does-not-compile"),
            pytest.param(None, True, id="no-template"),
        ],
    )
    def test_its_own_template(self, page_file: Path, text, guarding) -> None:
        assert _components(page_file, card=text).guarding("card") is guarding

    def test_an_unresolved_component_guards(self, page_file: Path) -> None:
        assert _components(page_file).guarding("ghost") is True

    @pytest.mark.parametrize(
        ("inner", "guarding"),
        [
            pytest.param(GUARDED_CHILDREN, True, id="guarding"),
            pytest.param(PLAIN_COMPONENT, False, id="plain"),
            pytest.param(None, True, id="no-template"),
        ],
    )
    def test_a_nested_component_is_followed(
        self, page_file: Path, inner, guarding
    ) -> None:
        components = _components(
            page_file, outer=wrap("inner", "{{ children }}"), inner=inner
        )
        assert components.guarding("outer") is guarding

    def test_a_prop_forwarded_into_a_guarding_component(self, page_file: Path) -> None:
        components = _components(
            page_file,
            outer='{% component "show" text=children %}',
            show=f"{_GUARD}{{{{ text }}}}{_END}",
        )
        assert components.guarding("outer") is True

    def test_an_unresolved_nested_component_guards(self, page_file: Path) -> None:
        components = _components(page_file, outer=wrap("ghost", "{{ children }}"))
        assert components.guarding("outer") is True

    def test_a_cycle_between_components_terminates(self, page_file: Path) -> None:
        components = _components(
            page_file, a=wrap("b", "{{ children }}"), b=wrap("a", "{{ children }}")
        )
        assert components.guarding("a") is False

    def test_the_answer_is_kept_per_name(self, page_file: Path) -> None:
        manager = _Manager(page_file.parent, card=GUARDED_CHILDREN)
        components = Components(manager, page_file)
        assert components.guarding("card") is components.guarding("card") is True
        assert manager.asked == ["card"]


class TestFollowedTemplates:
    """An extends or include is followed by a literal name, else it guards."""

    @pytest.mark.parametrize(
        ("parent", "guarding"),
        [
            pytest.param(
                f"<div>{_GUARD}{{% block b %}}{{% endblock %}}{_END}</div>",
                True,
                id="guarding",
            ),
            pytest.param("<div>{% block b %}{% endblock %}</div>", False, id="plain"),
        ],
    )
    def test_extends_a_literal_parent(
        self, page_file: Path, findable_templates, parent: str, guarding
    ) -> None:
        name = findable_templates("base.html", parent)
        text = (
            f'{{% extends "{name}" %}}{{% block b %}}{{{{ children }}}}{{% endblock %}}'
        )
        assert _components(page_file, card=text).guarding("card") is guarding

    @pytest.mark.parametrize(
        ("part", "guarding"),
        [
            pytest.param(f"{_GUARD}{{{{ children }}}}{_END}", True, id="guarding"),
            pytest.param("{{ children }}", False, id="plain"),
            pytest.param("{% if %}", True, id="does-not-compile"),
        ],
    )
    def test_includes_a_literal_template(
        self, page_file: Path, findable_templates, part: str, guarding
    ) -> None:
        name = findable_templates("part.html", part)
        text = f'<div>{{% include "{name}" %}}</div>'
        assert _components(page_file, card=text).guarding("card") is guarding

    def test_a_chain_of_includes_is_followed(
        self, page_file: Path, findable_templates
    ) -> None:
        last = findable_templates("last.html", f"{_GUARD}x{_END}")
        first = findable_templates("first.html", f'{{% include "{last}" %}}')
        text = f'{{% include "{first}" %}}'
        assert _components(page_file, card=text).guarding("card") is True

    def test_an_include_cycle_terminates(
        self, page_file: Path, findable_templates
    ) -> None:
        name = f"{page_file.parent.name}_self.html"
        findable_templates(
            "self.html", f'{{% if never %}}{{% include "{name}" %}}{{% endif %}}'
        )
        text = f'{{% include "{name}" %}}'
        assert _components(page_file, card=text).guarding("card") is False

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("{% include part %}", id="include-variable"),
            pytest.param('{% include "part.html"|lower %}', id="include-filtered"),
            pytest.param('{% include "nowhere-at-all.html" %}', id="include-missing"),
            pytest.param("{% extends base %}", id="extends-variable"),
            pytest.param('{% extends "nowhere-at-all.html" %}', id="extends-missing"),
        ],
    )
    def test_an_unfollowable_reference_guards(self, page_file: Path, text: str) -> None:
        assert _components(page_file, card=text).guarding("card") is True


class TestTemplateNames:
    """Only a template named by a plain string literal is followed."""

    def test_a_template_with_no_file_is_followed_without_a_stamp(
        self, page_file: Path, monkeypatch
    ) -> None:
        engine = Engine.get_default()
        monkeypatch.setattr(
            engine, "get_template", lambda _name: Template("{{ children }}")
        )
        components = _components(page_file, card='{% include "in-memory.html" %}')
        assert components.guarding("card") is False
        assert {path.name for path, _mtime in components.stamps} == {"card.djx"}


class TestMemoAndStamps:
    """A verdict is kept per anchor and text, with the files it read and when."""

    def test_the_same_anchor_and_text_is_read_once(self, page_file: Path) -> None:
        with patch.object(sources, "Template", wraps=Template) as compiled:
            assert _components(page_file, card=GUARDED_CHILDREN).guarding("card")
            assert _components(page_file, card=GUARDED_CHILDREN).guarding("card")
        assert compiled.call_count == 1

    def test_another_anchor_reads_the_text_again(self, tmp_path: Path) -> None:
        first, second = tmp_path / "one" / "page.py", tmp_path / "two" / "page.py"
        for path in (first, second):
            path.parent.mkdir()
            path.write_text("x = 1\n")
        with patch.object(sources, "Template", wraps=Template) as compiled:
            for path in (first, second):
                assert _components(path, card=GUARDED_CHILDREN).guarding("card")
        assert compiled.call_count == 2

    def test_stamps_name_the_component_and_the_include(
        self, page_file: Path, findable_templates
    ) -> None:
        part = findable_templates("part.html", "{{ children }}")
        components = _components(page_file, card=f'{{% include "{part}" %}}')
        components.guarding("card")
        stamped = {path.name for path, _mtime in components.stamps}
        assert stamped == {"card.djx", part}
        assert fresh(components.stamps) is True

    def test_fresh_notices_an_edit_and_a_removal(self, tmp_path: Path) -> None:
        kept, edited, removed = (tmp_path / name for name in ("a", "b", "c"))
        for path in (kept, edited, removed):
            path.write_text("x")
        stamps = frozenset(
            (path, path.stat().st_mtime) for path in (kept, edited, removed)
        )
        assert fresh(stamps) is True
        touch_later(edited, "y")
        assert fresh(stamps) is False
        assert fresh(frozenset({(kept, kept.stat().st_mtime)})) is True
        removed.unlink()
        assert fresh(frozenset({(removed, 1.0)})) is False

    @pytest.mark.parametrize(("debug", "guarding"), [(True, True), (False, False)])
    def test_an_edited_include_is_read_again_only_under_debug(
        self, page_file: Path, findable_templates, settings, debug, guarding
    ) -> None:
        part = findable_templates("part.html", "{{ children }}")
        text = f'{{% include "{part}" %}}'
        assert _components(page_file, card=text).guarding("card") is False
        touch_later(page_file.parent / "templates" / part, f"{_GUARD}x{_END}")
        _forget_cached_templates()
        settings.DEBUG = debug
        assert _components(page_file, card=text).guarding("card") is guarding


class TestGuardedZones:
    """`guarded_zones` names each zone a guarding component holds, no page block."""

    def _zones(self, page_file: Path, body: str, **sources) -> list:
        components = _components(page_file, **sources)
        return list(guarded_zones(Template(body).nodelist, components))

    def test_a_page_block_is_left_out(self, page_file: Path) -> None:
        body = f"{_GUARD}{SECRET_ZONE}{_END}"
        assert self._zones(page_file, body) == []

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(CHILDREN_BODY, id="children"),
            pytest.param(fill_slot("content"), id="slot"),
            pytest.param(fill_slot("aside"), id="any-slot"),
            pytest.param(NESTED_COMPONENT_BODY, id="nested-component"),
        ],
    )
    def test_every_zone_of_a_guarding_body(self, page_file: Path, body: str) -> None:
        zones = self._zones(
            page_file, body, guarded=GUARDED_SET_SLOT, plain=PLAIN_COMPONENT
        )
        assert zones == [("cz", "guarded")]

    def test_a_plain_body_is_left_out(self, page_file: Path) -> None:
        assert self._zones(page_file, wrap("plain"), plain=PLAIN_COMPONENT) == []

    def test_each_name_once(self, page_file: Path) -> None:
        page_block = f"{_GUARD}{SECRET_ZONE}{_END}"
        body = page_block + page_block + CHILDREN_BODY + CHILDREN_BODY
        zones = self._zones(page_file, body, guarded=GUARDED_CHILDREN)
        assert zones == [("cz", "guarded")]


class TestBoundZones:
    """`bound_zones` adds the zones a condition or a `{% has_perm %}` decides."""

    def test_checks_and_conditions(self, page_file: Path) -> None:
        body = (
            f'{_GUARD}{{% zone "a" %}}x{{% endzone %}}{_END}'
            '{% if c %}{% zone "b" %}x{% endzone %}{% endif %}'
            '{% for i in items %}{% zone "c" %}x{% endzone %}{% endfor %}'
            + wrap("guarded", '{% zone "d" %}x{% endzone %}')
            + '{% zone "e" %}x{% endzone %}'
        )
        components = _components(page_file, guarded=GUARDED_CHILDREN)
        assert bound_zones(Template(body).nodelist, components) == frozenset(
            {"a", "b", "c", "d"}
        )


_HOLDS_SLOT = "{% if c %}{{ slot_content }}{% endif %}"


def _into_slot(inner: str) -> str:
    slot = f'{{% #slot "content" %}}{inner}{{% /slot %}}'
    return f'{{% #component "inner" %}}{slot}{{% /component %}}'


class TestBodyFlow:
    """The body counts as decided through every name a template binds it to."""

    @pytest.mark.parametrize(
        ("text", "guarding"),
        [
            pytest.param(
                "{% with x=children %}{{ x }}{% endwith %}", False, id="alias"
            ),
            pytest.param(
                "{% with x=children %}{{ x|upper }}{% endwith %}",
                True,
                id="alias-filtered",
            ),
            pytest.param(
                '{% with x=children %}{{ x|default:"" }}{% endwith %}',
                False,
                id="alias-passing-filter",
            ),
            pytest.param(
                "{% with x=children %}{% if c %}{{ x }}{% endif %}{% endwith %}",
                True,
                id="alias-under-if",
            ),
            pytest.param(
                "{% with x=title %}{% if c %}{{ x }}{% endif %}{% endwith %}"
                "{{ children }}",
                False,
                id="unrelated-alias",
            ),
            pytest.param(
                "{% for c in children %}{{ c }}{% endfor %}", True, id="loop-variable"
            ),
            pytest.param(
                "{% for a, b in children %}{{ b }}{% endfor %}",
                True,
                id="unpacked-loop-variables",
            ),
            pytest.param('{{ "x"|default:children }}', True, id="filter-argument"),
            pytest.param(
                '{{ children|default:"x" }}', False, id="literal-filter-argument"
            ),
            pytest.param("{% regroup items by g as children %}", True, id="raw-name"),
            pytest.param('{% now "Y" %}{{ children }}', False, id="unrelated-tag"),
            pytest.param('{% firstof children "x" %}', True, id="tag-reads-body"),
            pytest.param(
                '{% load next_static %}{% use_style "a.css" %}{{ children }}',
                False,
                id="next-context-tag",
            ),
        ],
    )
    def test_children_of_one_template(self, page_file: Path, text, guarding) -> None:
        assert _components(page_file, card=text).guarding("card") is guarding

    @pytest.mark.usefixtures("guard_tags")
    def test_a_third_party_context_tag_decides(self, page_file: Path) -> None:
        text = "{% load guard_tags %}{% peek %}{{ children }}"
        assert _components(page_file, card=text).guarding("card") is True

    @pytest.mark.parametrize(
        ("slot", "guarding"),
        [
            pytest.param("{{ children }}", True, id="variable"),
            pytest.param('{% component "leaf" %}', True, id="component"),
            pytest.param(
                "{% with y=children %}{{ y }}{% endwith %}", True, id="other-tag"
            ),
            pytest.param("x", False, id="text"),
        ],
    )
    def test_children_handed_into_a_slot(self, page_file: Path, slot, guarding) -> None:
        components = _components(
            page_file, outer=_into_slot(slot), inner=_HOLDS_SLOT, leaf="{{ children }}"
        )
        assert components.guarding("outer") is guarding

    def test_a_slot_shown_plainly_is_not_decided(self, page_file: Path) -> None:
        components = _components(
            page_file, outer=_into_slot("{{ children }}"), inner="{{ slot_content }}"
        )
        assert components.guarding("outer") is False

    @pytest.mark.parametrize(
        ("slot", "guarding"),
        [
            pytest.param('{% set_slot "aside" %}', True, id="own-slot"),
            pytest.param('{% set_slot "footer" %}', False, id="other-slot"),
        ],
    )
    def test_a_slot_handed_on_into_a_slot(
        self, page_file: Path, slot, guarding
    ) -> None:
        components = _components(page_file, outer=_into_slot(slot), inner=_HOLDS_SLOT)
        assert components.guarding("outer", "slot_aside") is guarding

    def test_a_mapping_keyed_by_no_name_binds_nothing(self) -> None:
        node = TextNode("x")
        node.held = {1: Template("{{ children }}").nodelist[0].filter_expression}
        assert list(guards._bindings(node)) == []
