import contextlib
import dataclasses
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Group
from django.http import Http404
from django.template import Context, Template
from django.test import RequestFactory

from next.bridge import drive
from next.components import (
    ComponentContextConflictError,
    ComponentContextManager,
    ComponentInfo,
    components_manager,
    render_component,
)
from next.components.context import ContextFunction
from next.components.modules import Loaded, ModuleLoader
from next.components.renderers import (
    _RESERVED_CONTEXT_KEYS,
    COMPONENT_PROPS_CONTEXT_KEY,
    ComponentRenderer,
    ComponentScope,
    ComponentTemplateLoader,
    CompositeComponentRenderer,
    _BridgedContextRun,
)
from next.deps import Depends, ResolutionContext
from next.deps.cache import DependencyCache, RequestMemo, request_memo
from next.deps.markers import factory_key
from next.deps.resolver import current_resolver
from next.deps.scope import RequestScope
from next.marks import fragment_marks
from next.pages import page
from next.requests import next_of
from next.seeding import TEMPLATE_PATH_KEY
from tests.support import (
    bound_dependency,
    build_page_request,
    unified_view,
    write_page_chain,
)
from tests.support.components import build_composite_component, render_with


RESERVED_KEYS = sorted(_RESERVED_CONTEXT_KEYS)

LOOPING_PAGE = """
from next.deps import Depends
from next.pages import context


@context("title")
def title(value=Depends("site_label")):
    return value


@context("items")
def items():
    return ["alpha", "beta", "gamma"]
"""
LOOP_TEMPLATE = '{% for x in items %}{% component "badge" label=x %}{% endfor %}'
UPPER_COMPONENT = """
from next.components import context
from next.deps import Depends


@context("upper")
def upper(value=Depends("label_upper")):
    return value
"""
RENDERING_PAGE = (
    LOOPING_PAGE
    + f"""

def render(value=Depends("site_label")):
    return {LOOP_TEMPLATE!r}
"""
)

# The inventory as the docs publish it, pinned so a core-set change is deliberate.
PINNED_RESERVED_KEYS = frozenset(
    {
        "_component_props",
        "_static_collector",
        "children",
        "csrf_token",
        "current_action_anchor",
        "current_component_module_path",
        "current_page_module_path",
        "current_template_path",
        "request",
    }
)


class TestNamedDependenciesPerInstance:
    """Every component instance keeps its own named-dependency resolutions."""

    @pytest.mark.parametrize("dispatch", [False, True], ids=["get", "dispatch"])
    @pytest.mark.parametrize(
        "source", [LOOPING_PAGE, RENDERING_PAGE], ids=["template", "render"]
    )
    def test_looped_instances_resolve_their_own_value(
        self, tmp_path: Path, source: str, *, dispatch: bool
    ) -> None:
        """A form dispatch re-render shares its cache, but not one instance's values."""
        _mgr, info, module_path = build_composite_component(
            tmp_path / "badge", name="badge", template="<b>{{ label }}={{ upper }}</b>"
        )
        module_path.write_text(UPPER_COMPONENT)
        ModuleLoader().load(module_path)
        (leaf,) = write_page_chain(tmp_path, [("items", source)])
        (leaf.parent / "template.djx").write_text(LOOP_TEMPLATE)
        view = unified_view(page, leaf)
        with (
            bound_dependency("site_label", lambda: "SITE"),
            bound_dependency("label_upper", lambda label: label.upper()),
            patch.object(components_manager, "get_component", return_value=info),
        ):
            request = build_page_request()
            if dispatch:
                next_of(request).dep_cache = {}
            response = view(request)
        assert response.content.decode().count("<b>") == 3
        for label in ("alpha", "beta", "gamma"):
            assert f"<b>{label}={label.upper()}</b>" in response.content.decode()


class TestKeylessContextCollisions:
    """A bare `@component.context` may not overwrite props or reserved keys."""

    def test_prop_key_raises(self, tmp_path: Path) -> None:
        """Returning a key that the calling tag passed as a prop raises."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})
        context_data = {
            COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"}),
            "title": "from prop",
        }

        with pytest.raises(
            ComponentContextConflictError,
            match="Component 'card' context returns 'title'",
        ):
            render_with(mgr, info, context_data)

        assert context_data["title"] == "from prop"

    def test_the_conflict_names_the_component_the_function_and_the_keys(
        self, tmp_path: Path
    ) -> None:
        mgr, info, module_path = build_composite_component(tmp_path)

        def defaults() -> dict[str, str]:
            return {"title": "from context", "kept": "yes"}

        mgr._registry.register(module_path, None, defaults)
        context_data = {COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"})}
        with pytest.raises(ComponentContextConflictError) as caught:
            render_with(mgr, info, context_data)
        error = caught.value
        assert (error.component, error.function, error.keys) == (
            "card",
            "defaults",
            ("title",),
        )
        assert "the keyless merge of 'defaults' never" in str(error)

    def test_reserved_inventory_is_pinned(self) -> None:
        """Adding or dropping a reserved key is a decision, not a silent edit."""
        assert _RESERVED_CONTEXT_KEYS == PINNED_RESERVED_KEYS

    @pytest.mark.parametrize("key", RESERVED_KEYS)
    def test_reserved_key_raises_and_keeps_the_render_value(
        self, key: str, tmp_path: Path
    ) -> None:
        """Every reserved key raises and the value the render path put there stands."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {key: "hijacked"})
        original = object()
        context_data = {key: original}

        with pytest.raises(ValueError, match=f"returns {key!r}"):
            render_with(mgr, info, context_data)

        assert context_data[key] is original

    def test_non_string_keys_merge_without_raising(self, tmp_path: Path) -> None:
        """A dict keyed by non-strings survives the slot-prefix scan."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="{{ ok }}"
        )
        mgr._registry.register(module_path, None, lambda: {1: "one", "ok": "yes"})
        context_data = {COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"})}

        assert render_with(mgr, info, context_data) == "yes"

    def test_slot_prefixed_key_raises(self, tmp_path: Path) -> None:
        """Any `slot_` prefixed key is reserved for slot content."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"slot_footer": "hijacked"})
        context_data = {"slot_footer": "<footer/>"}

        with pytest.raises(ValueError, match="'slot_footer'"):
            render_with(mgr, info, context_data)

        assert context_data["slot_footer"] == "<footer/>"

    def test_message_lists_every_conflict(self, tmp_path: Path) -> None:
        """The message names all conflicting keys in sorted order."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(
            module_path, None, lambda: {"slot_a": 1, "children": 2, "kept": 3}
        )

        with pytest.raises(ValueError, match="returns 'children', 'slot_a',"):
            render_with(mgr, info, {})


class TestKeylessContextMerges:
    """Names outside the guarded domain keep merging silently."""

    def test_non_conflicting_keys_merge(self, tmp_path: Path) -> None:
        """A dict that sets no guarded key is merged into the context."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="{{ env }}"
        )
        mgr._registry.register(module_path, None, lambda: {"env": "prod"})
        context_data = {COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"})}

        assert render_with(mgr, info, context_data) == "prod"

    def test_inherited_page_key_is_shadowed(self, tmp_path: Path) -> None:
        """A page key that is not a prop of this call may be shadowed."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="{{ title }}|{{ variant }}"
        )
        mgr._registry.register(module_path, None, lambda: {"title": "component wins"})
        context_data = {
            COMPONENT_PROPS_CONTEXT_KEY: frozenset({"variant"}),
            "title": "page title",
            "variant": "wide",
        }

        assert render_with(mgr, info, context_data) == "component wins|wide"

    def test_same_key_merges_when_tag_passes_no_prop(self, tmp_path: Path) -> None:
        """The guard follows the call site, so a propless call still merges."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})
        context_data = {COMPONENT_PROPS_CONTEXT_KEY: frozenset()}

        assert render_with(mgr, info, context_data) == "<div>from context</div>"

    def test_reading_a_prop_and_returning_a_new_key_works(self, tmp_path: Path) -> None:
        """The supported way to build on a prop is a parameter named after it."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="{{ headline }}|{{ title }}"
        )

        def headline(title: str) -> dict:
            return {"headline": title.upper()}

        mgr._registry.register(module_path, None, headline)
        context_data = {
            COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"}),
            "title": "hello",
        }

        assert render_with(mgr, info, context_data) == "HELLO|hello"

    def test_a_keyless_return_that_is_no_dict_merges_nothing(
        self, tmp_path: Path
    ) -> None:
        """Only a mapping names keys, so anything else leaves the context alone."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="{{ env }}"
        )
        mgr._registry.register(module_path, None, lambda: "not a mapping")

        assert render_with(mgr, info, {"env": "prod"}) == "prod"

    def test_keyed_context_still_overwrites(self, tmp_path: Path) -> None:
        """A keyed registration names one key explicitly and is not guarded."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="{{ children }}"
        )
        mgr._registry.register(module_path, "children", lambda: "keyed")
        context_data = {"children": "<i>body</i>"}

        assert render_with(mgr, info, context_data) == "keyed"


class TestRenderComponentWithoutTag:
    """A direct `render_component` carries no prop names, so only keys are reserved."""

    def test_plain_key_merges(self, tmp_path: Path) -> None:
        """Without `_component_props` an ordinary key merges as before."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})

        with patch("next.components.renderers.component", mgr):
            html = render_component(info, {"title": "caller"})

        assert "from context" in html

    def test_reserved_key_still_raises(self, tmp_path: Path) -> None:
        """The reserved names are guarded even without a calling tag."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"children": "x"})

        with (
            patch("next.components.renderers.component", mgr),
            pytest.raises(ValueError, match="'children'"),
        ):
            render_component(info, {})


class TestComponentTagPropsChannel:
    """`{% component %}` publishes its prop names so the guard can see them."""

    def _render(self, mgr: ComponentContextManager, info: ComponentInfo, source: str):
        template = Template("{% load components %}" + source)
        context = Context(
            {"current_template_path": str(info.scope_root / "template.djx")}
        )
        with (
            patch.object(components_manager, "get_component", return_value=info),
            patch("next.components.renderers.component", mgr),
        ):
            return template.render(context)

    def test_prop_collision_raises_through_the_tag(self, tmp_path: Path) -> None:
        """A prop named on the tag is guarded during a real render."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "hijacked"})

        with pytest.raises(
            ComponentContextConflictError,
            match="Component 'card' context returns 'title'",
        ):
            self._render(mgr, info, '{% component "card" title="Hi" %}')

    def test_no_prop_on_the_tag_merges_through_the_tag(self, tmp_path: Path) -> None:
        """The same component without that prop keeps merging."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})

        html = self._render(mgr, info, '{% component "card" %}')

        assert "from context" in html

    def test_prop_value_reaches_a_context_parameter(self, tmp_path: Path) -> None:
        """A context function reads the prop by parameter name during a render."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="<div>{{ headline }}</div>"
        )

        def headline(title: str) -> dict:
            return {"headline": title.upper()}

        mgr._registry.register(module_path, None, headline)

        html = self._render(mgr, info, '{% component "card" title="Hi" %}')

        assert "HI" in html

    def test_slot_content_is_guarded_through_the_tag(self, tmp_path: Path) -> None:
        """Slot HTML gathered by the tag cannot be overwritten by the context."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"slot_body": "hijacked"})

        with pytest.raises(ValueError, match="'slot_body'"):
            self._render(
                mgr,
                info,
                '{% #component "card" %}{% #slot "body" %}ok{% /slot %}{% /component %}',
            )


_RENDER_MODULE = """def render(title):
    return f"<b>{title}</b>"
"""


class TestCallerContextOwnership:
    """`render_component` reads the caller's mapping and never writes to it."""

    def test_simple_render_leaves_the_caller_dict_untouched(
        self, tmp_path: Path, rf: RequestFactory
    ) -> None:
        """The request and csrf keys are set on the render copy, not on the argument."""
        (tmp_path / "card.djx").write_text("<div>{{ title }} {{ csrf_token }}</div>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        context_data = {"title": "Hi"}
        request = rf.get("/")

        html = render_component(info, context_data, request=request)

        assert "Hi" in html
        assert context_data == {"title": "Hi"}

    def test_composite_context_functions_write_to_the_render_copy(
        self, tmp_path: Path
    ) -> None:
        """A `@component.context` merge stays inside the render copy."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="<div>{{ title }} {{ env }}</div>"
        )
        mgr._registry.register(module_path, None, lambda: {"env": "prod"})
        context_data = {"title": "Hi"}

        with patch("next.components.renderers.component", mgr):
            html = render_component(info, context_data)

        assert "prod" in html
        assert context_data == {"title": "Hi"}

    def test_composite_render_function_leaves_the_caller_dict_untouched(
        self, tmp_path: Path
    ) -> None:
        """The `render()` branch resolves from the caller's mapping read-only."""
        mgr, info, module_path = build_composite_component(tmp_path)
        module_path.write_text(_RENDER_MODULE)
        context_data = {"title": "Hi"}

        with patch("next.components.renderers.component", mgr):
            html = render_component(info, context_data)

        assert html == "<b>Hi</b>"
        assert context_data == {"title": "Hi"}

    def test_an_unloadable_module_renders_nothing(self, tmp_path: Path) -> None:
        """Its access declarations are unknown, so the template stays out."""
        mgr, info, _module_path = build_composite_component(
            tmp_path, template="<div>{{ title }}</div>"
        )

        with (
            patch("next.components.renderers.component", mgr),
            patch.object(ModuleLoader, "loaded", return_value=Loaded(None)),
        ):
            html = render_component(info, {"title": "Hi"})

        assert html == ""


_ASYNC_RENDER_MODULE = """
async def render(title):
    return f"<b>{title}</b>"
"""


class _CountingBridge:
    """Bridge that counts its calls, driving the work unless told to refuse it."""

    def __init__(self, *, refuse: bool = False) -> None:
        """Start with no call counted and no bridge running."""
        self.calls = 0
        self.refuse = refuse
        self.inside = False

    def __call__(self, work) -> object:
        self.calls += 1
        if self.refuse:
            msg = "a sync component never reaches the bridge"
            raise AssertionError(msg)
        self.inside = True
        try:
            return drive(work)
        finally:
            self.inside = False


@pytest.fixture()
def bridge_calls():
    """Count each bridged render through a driver that wraps the real one."""
    counting = _CountingBridge()
    with patch("next.deps.runner.drive", counting):
        yield counting


@pytest.fixture()
def refusing_bridge():
    """Fail any bridged call, so a sync render must not reach the driver."""
    refusing = _CountingBridge(refuse=True)
    with patch("next.deps.runner.drive", refusing):
        yield refusing


class TestSyncComponentsNeverBridge:
    """A component with no async part renders without touching the bridge."""

    def test_sync_context_functions(self, tmp_path: Path, refusing_bridge) -> None:
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, "title", lambda: "sync")
        assert render_with(mgr, info, {}) == "<div>sync</div>"
        assert refusing_bridge.calls == 0

    def test_sync_render(self, tmp_path: Path, refusing_bridge) -> None:
        _mgr, info, module_path = build_composite_component(tmp_path)
        module_path.write_text(_RENDER_MODULE)
        assert render_component(info, {"title": "Hi"}) == "<b>Hi</b>"
        assert refusing_bridge.calls == 0


class TestInlineAsyncComponents:
    """The async parts of one inline component instance run inside one bridge."""

    def test_an_async_context_function(self, tmp_path: Path, bridge_calls) -> None:
        mgr, info, module_path = build_composite_component(tmp_path)

        async def title() -> str:
            return "async"

        mgr._registry.register(module_path, "title", title)
        assert render_with(mgr, info, {}) == "<div>async</div>"
        assert bridge_calls.calls == 1

    def test_an_async_render(self, tmp_path: Path, bridge_calls) -> None:
        _mgr, info, module_path = build_composite_component(tmp_path)
        module_path.write_text(_ASYNC_RENDER_MODULE)
        assert render_component(info, {"title": "Hi"}) == "<b>Hi</b>"
        assert bridge_calls.calls == 1

    def test_an_async_render_returning_a_response(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        _mgr, info, module_path = build_composite_component(tmp_path)
        module_path.write_text(
            "from django.http import HttpResponse\n\n"
            "async def render():\n"
            "    return HttpResponse('<i>raw</i>')\n"
        )
        assert render_component(info, {}) == "<i>raw</i>"
        assert bridge_calls.calls == 1

    def test_mixed_functions_match_a_sequential_run_in_one_bridge(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        """A later function reads what an earlier one wrote, whatever their colour."""
        template = "{{ a }}|{{ b }}|{{ c }}|{{ d }}"
        mgr, info, module_path = build_composite_component(
            tmp_path / "mixed", template=template
        )
        reference, sync_info, sync_path = build_composite_component(
            tmp_path / "sync", template=template
        )

        async def b(a: str) -> str:
            return a + "B"

        async def d() -> dict[str, str]:
            return {"d": "D"}

        for registry, path, middle, spread in (
            (mgr._registry, module_path, b, d),
            (reference._registry, sync_path, lambda a: a + "B", lambda: {"d": "D"}),
        ):
            registry.register(path, "a", lambda: "A")
            registry.register(path, "b", middle)
            registry.register(path, "c", lambda b: b + "C")
            registry.register(path, None, spread)

        assert render_with(mgr, info, {}) == "A|AB|ABC|D"
        assert bridge_calls.calls == 1
        assert render_with(reference, sync_info, {}) == "A|AB|ABC|D"
        assert bridge_calls.calls == 1

    def test_each_instance_bridges_once(self, tmp_path: Path, bridge_calls) -> None:
        mgr, info, module_path = build_composite_component(tmp_path)

        async def first() -> str:
            return "1"

        async def second() -> str:
            return "2"

        mgr._registry.register(module_path, "first", first)
        mgr._registry.register(module_path, "title", second)
        for _ in range(3):
            assert render_with(mgr, info, {}) == "<div>2</div>"
        assert bridge_calls.calls == 3

    @pytest.mark.django_db()
    def test_orm_access_lands_on_the_rendering_thread(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        Group.objects.create(name="editors")
        mgr, info, module_path = build_composite_component(tmp_path)
        threads: list[int] = []

        async def title() -> str:
            threads.append(await sync_to_async(threading.get_ident)())
            group = await Group.objects.aget(name="editors")
            return group.name

        mgr._registry.register(module_path, "title", title)
        assert render_with(mgr, info, {}) == "<div>editors</div>"
        assert threads == [threading.get_ident()]

    def test_an_error_keeps_its_type(self, tmp_path: Path, bridge_calls) -> None:
        mgr, info, module_path = build_composite_component(tmp_path)

        async def title() -> str:
            msg = "no such card"
            raise Http404(msg)

        mgr._registry.register(module_path, "title", title)
        with pytest.raises(Http404, match="no such card"):
            render_with(mgr, info, {})


class TestBridgedContextRun:
    """A stretch of a bridged run is one bridge, and a finished run bridges no more."""

    def _run(self, tmp_path: Path, *functions: ContextFunction) -> _BridgedContextRun:
        _mgr, info, _module_path = build_composite_component(tmp_path)
        return _BridgedContextRun(info, {}, None, functions)

    def test_a_run_to_a_key_stops_after_it(self, tmp_path: Path, bridge_calls) -> None:
        async def first() -> str:
            return "1"

        run = self._run(
            tmp_path,
            ContextFunction(first, "first", is_async=True),
            ContextFunction(lambda: "2", "second"),
        )
        run.run_to("first")
        assert run.context_data == {"first": "1"}
        assert run.pending("second")
        run.run_to()
        assert run.context_data == {"first": "1", "second": "2"}
        run.run_to()
        assert bridge_calls.calls == 1

    def test_a_run_to_a_key_no_function_holds_runs_them_all(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        async def first() -> str:
            return "1"

        run = self._run(
            tmp_path,
            ContextFunction(first, "first", is_async=True),
            ContextFunction(lambda: "2", "second"),
        )
        run.run_to("absent")
        assert run.context_data == {"first": "1", "second": "2"}
        assert bridge_calls.calls == 1

    def test_sync_around_async_bridges_the_async_one_alone(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        """The sync functions on either side run on the rendering thread directly."""
        inside: dict[str, bool] = {}

        def lead() -> str:
            inside["lead"] = bridge_calls.inside
            return "a"

        async def middle(lead: str) -> str:
            return lead + "b"

        def tail(middle: str) -> str:
            inside["tail"] = bridge_calls.inside
            return middle + "c"

        run = self._run(
            tmp_path,
            ContextFunction(lead, "lead"),
            ContextFunction(middle, "middle", is_async=True),
            ContextFunction(tail, "tail"),
        )
        run.run_to()
        assert run.context_data == {"lead": "a", "middle": "ab", "tail": "abc"}
        assert inside == {"lead": False, "tail": False}
        assert bridge_calls.calls == 1

    def test_sync_between_async_ones_shares_their_bridge(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        """The span from the first async function to the last is one bridge."""
        inside: dict[str, bool] = {}

        async def first() -> str:
            return "a"

        def between(first: str) -> str:
            inside["between"] = bridge_calls.inside
            return first + "b"

        async def last(between: str) -> str:
            return between + "c"

        run = self._run(
            tmp_path,
            ContextFunction(first, "first", is_async=True),
            ContextFunction(between, "between"),
            ContextFunction(last, "last", is_async=True),
        )
        run.run_to()
        assert run.context_data == {"first": "a", "between": "ab", "last": "abc"}
        assert inside == {"between": True}
        assert bridge_calls.calls == 1

    def test_an_access_read_in_the_middle_splits_the_span(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        """A read stops after its function, so the rest bridges again only if async."""
        inside: dict[str, bool] = {}

        async def first() -> str:
            return "a"

        async def owner(first: str) -> str:
            return first + "b"

        def after(owner: str) -> str:
            inside["after"] = bridge_calls.inside
            return owner + "c"

        async def last(after: str) -> str:
            return after + "d"

        run = self._run(
            tmp_path,
            ContextFunction(first, "first", is_async=True),
            ContextFunction(owner, "owner", is_async=True),
            ContextFunction(after, "after"),
            ContextFunction(last, "last", is_async=True),
        )
        run.run_to("owner")
        assert run.context_data == {"first": "a", "owner": "ab"}
        assert bridge_calls.calls == 1
        run.run_to()
        assert run.context_data["last"] == "abcd"
        assert inside == {"after": False}
        assert bridge_calls.calls == 2

    def test_a_read_of_a_sync_function_before_any_async_one_bridges_nothing(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        async def late() -> str:
            return "b"

        run = self._run(
            tmp_path,
            ContextFunction(lambda: "a", "owner"),
            ContextFunction(late, "late", is_async=True),
        )
        run.run_to("owner")
        assert run.context_data == {"owner": "a"}
        assert bridge_calls.calls == 0
        run.run_to()
        assert run.context_data == {"owner": "a", "late": "b"}
        assert bridge_calls.calls == 1

    def test_serialized_values_wait_in_order(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        async def keyed() -> str:
            return "k"

        async def spread() -> dict[str, str]:
            return {"s": "S"}

        async def nothing() -> None:
            return None

        run = self._run(
            tmp_path,
            ContextFunction(keyed, "keyed", serialize=True, is_async=True),
            ContextFunction(spread, None, serialize=True, is_async=True),
            ContextFunction(nothing, "quiet", is_async=True),
        )
        run.run_to()
        assert run.serialized == [("keyed", "k", None), ("s", "S", None)]
        assert run.context_data == {"keyed": "k", "s": "S", "quiet": None}

    def test_a_keyless_value_that_is_no_dict_is_dropped(
        self, tmp_path: Path, bridge_calls
    ) -> None:
        async def stray() -> str:
            return "stray"

        run = self._run(tmp_path, ContextFunction(stray, None, is_async=True))
        run.run_to()
        assert run.context_data == {}


async def _shout(label: str) -> str:
    return label.upper()


_SHOUTING_RENDER_MODULE = """
from next.deps import Depends


async def render(first=Depends("shout"), second=Depends("shout")):
    return f"<b>{first}{second}</b>"
"""


@contextlib.contextmanager
def _page_scope(*shared: str, seed: dict[str, object] | None = None):
    """Render the block under a request scope, the way an async render unit holds one."""
    scope = RequestScope(seed)
    token = request_memo.set(RequestMemo(scope, frozenset(shared)))
    try:
        yield scope
    finally:
        request_memo.reset(token)


class TestInstancesUnderARequestScope:
    """Two instances of one component never read each other's values from the scope."""

    @pytest.mark.parametrize("module", [None, "raise LookupError\n", "# empty\n"])
    def test_a_render_resolving_nothing_takes_no_layer(
        self, tmp_path: Path, module: str | None
    ) -> None:
        _mgr, info, module_path = build_composite_component(
            tmp_path, template="<b>{{ title }}</b>"
        )
        if module is None:
            info = dataclasses.replace(info, module_path=None)
        else:
            module_path.write_text(module)
        loader = ModuleLoader()
        renderer = CompositeComponentRenderer(loader, ComponentTemplateLoader(loader))
        with (
            _page_scope(),
            patch.object(RequestMemo, "instance", side_effect=AssertionError),
        ):
            html = renderer.render(info, {"title": "x"}, None)
        assert html == ("<b>x</b>" if module == "# empty\n" else "")

    def _shouting(
        self, tmp_path: Path, title
    ) -> tuple[ComponentContextManager, ComponentInfo]:
        mgr, info, module_path = build_composite_component(
            tmp_path, template="<b>{{ title }}</b>"
        )
        mgr._registry.register(module_path, "title", title)
        return mgr, info

    def test_a_factory_two_parameters_share(self, tmp_path: Path) -> None:
        async def title(first=Depends(_shout), second=Depends(_shout)) -> str:
            return first + second

        mgr, info = self._shouting(tmp_path, title)
        with _page_scope():
            assert render_with(mgr, info, {"label": "x"}) == "<b>XX</b>"
            assert render_with(mgr, info, {"label": "y"}) == "<b>YY</b>"

    def test_a_named_dependency_the_page_shares(self, tmp_path: Path) -> None:
        async def title(value=Depends("shout")) -> str:
            return value

        mgr, info = self._shouting(tmp_path, title)
        with bound_dependency("shout", _shout), _page_scope("shout"):
            assert render_with(mgr, info, {"label": "x"}) == "<b>X</b>"
            assert render_with(mgr, info, {"label": "y"}) == "<b>Y</b>"

    def test_a_sync_context_callable_bridging_a_factory(self, tmp_path: Path) -> None:
        key = factory_key(_shout)

        def title(first=Depends(_shout)) -> str:
            return first

        mgr, info = self._shouting(tmp_path, title)
        with _page_scope(key):
            assert render_with(mgr, info, {"label": "x"}) == "<b>X</b>"
            assert render_with(mgr, info, {"label": "y"}) == "<b>Y</b>"

    def test_an_async_render_function(self, tmp_path: Path) -> None:
        _mgr, info, module_path = build_composite_component(tmp_path)
        module_path.write_text(_SHOUTING_RENDER_MODULE)
        with bound_dependency("shout", _shout), _page_scope():
            assert render_component(info, {"label": "x"}) == "<b>XX</b>"
            assert render_component(info, {"label": "y"}) == "<b>YY</b>"

    def test_instances_in_a_loop(self, tmp_path: Path) -> None:
        async def upper(value=Depends("shout"), again=Depends("shout")) -> str:
            return value + again

        mgr, info, module_path = build_composite_component(
            tmp_path / "badge", name="badge", template="<b>{{ label }}={{ upper }}</b>"
        )
        mgr._registry.register(module_path, "upper", upper)
        template = Template(
            "{% load components %}"
            '{% for x in items %}{% component "badge" label=x %}{% endfor %}'
        )
        with (
            bound_dependency("shout", _shout),
            patch("next.components.renderers.component", mgr),
            patch("next.components.modules.component", mgr),
            patch.object(components_manager, "get_component", return_value=info),
            _page_scope("shout"),
        ):
            html = template.render(
                Context(
                    {
                        "items": ["alpha", "beta", "gamma"],
                        TEMPLATE_PATH_KEY: str(tmp_path / "template.djx"),
                    }
                )
            )
        assert html == "".join(
            f"<b>{label}={label.upper() * 2}</b>"
            for label in ("alpha", "beta", "gamma")
        )

    def test_a_value_the_page_resolved_is_read_by_every_instance(
        self, tmp_path: Path
    ) -> None:
        calls: list[str] = []

        async def site() -> str:
            calls.append("site")
            return "SITE"

        async def title(value=Depends("site")) -> str:
            return value

        mgr, info = self._shouting(tmp_path, title)
        with bound_dependency("site", site), _page_scope("site") as scope:
            context = ResolutionContext(None, None, {}, {}, DependencyCache())
            drive(current_resolver().acall_factory(site, "site", context))
            assert render_with(mgr, info, {}) == "<b>SITE</b>"
            assert render_with(mgr, info, {}) == "<b>SITE</b>"
            assert scope.get("site") == "SITE"
        assert calls == ["site"]


class TestScope:
    """A scope reads the call-site layers in place and writes its own keys alone."""

    def test_reads_fall_through_and_writes_stay_own(self) -> None:
        caller = {"title": "caller", "seen": 1}
        scope = ComponentScope({"title": "own"}, [caller])
        scope["extra"] = 2
        del scope["extra"]
        assert (scope["title"], scope["seen"], "seen" in scope) == ("own", 1, True)
        assert (dict(scope), len(scope)) == ({"title": "own", "seen": 1}, 2)
        assert caller == {"title": "caller", "seen": 1}
        with pytest.raises(KeyError):
            scope["missing"]

    def test_a_derived_scope_leaves_its_base(self) -> None:
        base = ComponentScope({"title": "own"}, [])
        derived = base.derived()
        derived["title"] = "derived"
        assert (base["title"], derived["title"]) == ("own", "derived")

    def test_a_cycle_variable_held_by_an_own_layer_is_set_there(self) -> None:
        own = {"row": "prop"}
        context = ComponentScope(own, [{"row": "caller"}]).context()
        with context.push(row="pushed"):
            Template('{% cycle "a" "b" as row silent %}').render(context)
            assert context["row"] == "a"
        assert own == {"row": "prop"}

    def test_a_cycle_variable_held_nowhere_lands_on_top(self) -> None:
        own: dict[str, object] = {}
        context = ComponentScope(own, [{}]).context()
        Template('{% cycle "a" "b" as row silent %}').render(context)
        assert own == {"row": "a"}


class _ProjectStrategy:
    """A strategy of the project that writes a navigation the source never shows."""

    def can_render(self, info: ComponentInfo) -> bool:
        return True

    def render(self, info: ComponentInfo, context_data: object, request: object) -> str:
        return '<nav><a href="/">H</a></nav>'


class TestProjectStrategies:
    """A strategy of the project has its output marked whole and read as unseen."""

    def test_its_output_runs_the_bound_pass(self, tmp_path: Path) -> None:
        seen: list[str] = []

        def marks(html: str) -> str:
            seen.append(html)
            return html

        info = ComponentInfo("c", tmp_path, "", None, None, is_simple=True)
        renderer = ComponentRenderer([_ProjectStrategy()])
        token = fragment_marks.set(marks)
        try:
            html = renderer.render(info, {})
        finally:
            fragment_marks.reset(token)
        assert seen == [html]
        assert renderer.templated(info) is False

    def test_no_strategy_renders_nothing_a_plan_can_read(self, tmp_path: Path) -> None:
        info = ComponentInfo("c", tmp_path, "", None, None, is_simple=True)
        renderer = ComponentRenderer([])
        assert (renderer.render(info, {}), renderer.templated(info)) == ("", True)
