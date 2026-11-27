from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeStateCase:
    """Modules fed to the node state scanner, keyed by path, and what it reports."""

    id: str
    sources: tuple[tuple[str, str], ...]
    expected: tuple[str, ...]


_RENDER = (
    "from django.template import Node\n"
    "\n"
    "\n"
    "class Sample(Node):\n"
    "    def render(self, context):\n"
    "        STATEMENT\n"
    "        return ''\n"
)

_MUTATORS = (
    "append",
    "update",
    "setdefault",
    "extend",
    "add",
    "insert",
    "pop",
    "clear",
    "remove",
    "discard",
    "popitem",
    "sort",
    "reverse",
    "move_to_end",
)


def _render_case(case_id: str, statement: str, line: int = 6) -> NodeStateCase:
    source = _RENDER.replace("STATEMENT", statement)
    return NodeStateCase(
        case_id, (("sample.py", source),), (f"sample:{line} Sample.render",)
    )


def _clean_case(case_id: str, statement: str) -> NodeStateCase:
    source = _RENDER.replace("STATEMENT", statement)
    return NodeStateCase(case_id, (("sample.py", source),), ())


_HELPER = (
    "from django.template import Node\n"
    "\n"
    "\n"
    "class Sample(Node):\n"
    "    def render(self, context):\n"
    "        return self.remember(context)\n"
    "\n"
    "    def remember(self, context):\n"
    "        self.seen = context\n"
    "        return ''\n"
)

_TRANSITIVE = (
    "from django.template import Node\n"
    "\n"
    "\n"
    "class Sample(Node):\n"
    "    def render(self, context):\n"
    "        return self.first(context)\n"
    "\n"
    "    def first(self, context):\n"
    "        return self.second(context)\n"
    "\n"
    "    def second(self, context):\n"
    "        self.seen = context\n"
    "        return ''\n"
)

_INHERITED_HELPER = (
    "from django.template import Node\n"
    "\n"
    "\n"
    "class Base(Node):\n"
    "    def remember(self, context):\n"
    "        self.seen = context\n"
    "        return ''\n"
    "\n"
    "\n"
    "class Sample(Base):\n"
    "    def render(self, context):\n"
    "        return self.remember(context)\n"
)

_OVERRIDDEN_HOOK = (
    "from django.template import Node\n"
    "\n"
    "\n"
    "class Base(Node):\n"
    "    def render(self, context):\n"
    "        return self.hook(context)\n"
    "\n"
    "    def hook(self, context):\n"
    "        return ''\n"
    "\n"
    "\n"
    "class Sample(Base):\n"
    "    def hook(self, context):\n"
    "        self.seen = context\n"
    "        return ''\n"
)

_SUPER_CALL = (
    "from django.template import Node\n"
    "\n"
    "\n"
    "class Remembering:\n"
    "    def remember(self, context):\n"
    "        self.seen = context\n"
    "        return ''\n"
    "\n"
    "\n"
    "class Sample(Remembering, Node):\n"
    "    def render(self, context):\n"
    "        return self.remember(context)\n"
    "\n"
    "    def remember(self, context):\n"
    "        return super().remember(context)\n"
)

_LOCAL_BASE = (
    "from django.template.base import Node\n"
    "\n"
    "\n"
    "class Base(Node):\n"
    "    pass\n"
    "\n"
    "\n"
    "class Sample(Base):\n"
    "    def render(self, value):\n"
    "        self.seen = value\n"
)

_MODULE_ALIAS = (
    "from django import template\n"
    "\n"
    "\n"
    "class Sample(template.Node):\n"
    "    def render(self, value):\n"
    "        self.seen = value\n"
)

_DJANGO_SUBCLASS = (
    "from django.template.library import SimpleNode\n"
    "\n"
    "\n"
    "class Sample(SimpleNode):\n"
    "    def render(self, value):\n"
    "        self.seen = value\n"
)

_RENDERABLE = (
    "class Sample:\n    def render(self, context):\n        self.seen = context\n"
)

_REEXPORTED_BASE = (
    "from django.template.base import Node\n\n\nclass Base(Node):\n    pass\n"
)

_REEXPORTING_TAGS = (
    "from pkg import Base\n"
    "\n"
    "\n"
    "class Sample(Base):\n"
    "    def render(self, value):\n"
    "        self.seen = value\n"
)

_REEXPORT = (
    ("pkg/__init__.py", "from .base import Base\n"),
    ("pkg/base.py", _REEXPORTED_BASE),
    ("pkg/tags.py", _REEXPORTING_TAGS),
)

NODE_STATE_VIOLATION_CASES: tuple[NodeStateCase, ...] = (
    _render_case("assignment", "self.seen = context"),
    _render_case("annotated_assignment", "self.seen: object = context"),
    _render_case("nested_attribute", "self.state.seen = context"),
    _render_case("unpacking", "self.first, *self.rest = context"),
    _render_case("augmented_assignment", "self.count += 1"),
    _render_case("subscript_assignment", "self.cache[context] = 1"),
    _render_case("subscript_deletion", "del self.cache[context]"),
    _render_case("attribute_deletion", "del self.cache"),
    *(
        _render_case(f"call_{name}", f"self.items.{name}(context)")
        for name in _MUTATORS
    ),
    _render_case("setattr", "setattr(self, 'seen', context)"),
    _render_case("delattr", "delattr(self, 'seen')"),
    _render_case("object_setattr", "object.__setattr__(self, 'seen', context)"),
    _render_case("object_delattr", "object.__delattr__(self, 'seen')"),
    _render_case("dunder_setattr", "self.__setattr__('seen', context)"),
    _render_case("super_setattr", "super().__setattr__('seen', context)"),
    _render_case(
        "alias_subscript", "cache = self.cache\n        cache[context] = 1", line=7
    ),
    _render_case(
        "alias_attribute", "state = self.state\n        state.seen = context", line=7
    ),
    _render_case(
        "alias_call", "items = self.items\n        items.append(context)", line=7
    ),
    _render_case(
        "alias_setattr",
        "state = self.state\n        setattr(state, 'seen', context)",
        line=7,
    ),
    _render_case(
        "alias_chain",
        "items = self.items\n        more = items\n        more.sort()",
        line=8,
    ),
    _render_case(
        "alias_walrus",
        "if (cache := self.cache) is not None:\n            cache[context] = 1",
        line=7,
    ),
    _render_case("self_alias", "node = self\n        node.seen = context", line=7),
    NodeStateCase(
        "helper_method", (("sample.py", _HELPER),), ("sample:9 Sample.remember",)
    ),
    NodeStateCase(
        "transitive_helper", (("sample.py", _TRANSITIVE),), ("sample:12 Sample.second",)
    ),
    NodeStateCase(
        "inherited_helper",
        (("sample.py", _INHERITED_HELPER),),
        ("sample:6 Base.remember",),
    ),
    NodeStateCase(
        "overridden_hook",
        (("sample.py", _OVERRIDDEN_HOOK),),
        ("sample:14 Sample.hook",),
    ),
    NodeStateCase(
        "super_call", (("sample.py", _SUPER_CALL),), ("sample:6 Remembering.remember",)
    ),
    NodeStateCase(
        "local_base", (("sample.py", _LOCAL_BASE),), ("sample:10 Sample.render",)
    ),
    NodeStateCase(
        "module_alias", (("sample.py", _MODULE_ALIAS),), ("sample:6 Sample.render",)
    ),
    NodeStateCase(
        "django_subclass",
        (("sample.py", _DJANGO_SUBCLASS),),
        ("sample:6 Sample.render",),
    ),
    NodeStateCase(
        "renderable", (("sample.py", _RENDERABLE),), ("sample:3 Sample.render",)
    ),
    NodeStateCase("reexported_base", _REEXPORT, ("pkg.tags:6 Sample.render",)),
)

_CLEAN = (
    "from django.template import Node\n"
    "\n"
    "\n"
    "class Sample(Node):\n"
    "    def __init__(self, nodelist):\n"
    "        self.nodelist = nodelist\n"
    "        self.cache = {}\n"
    "\n"
    "    def render(self, context):\n"
    "        slots = {}\n"
    "        slots[self.nodelist] = context\n"
    "        context.render_context[self] = slots\n"
    "        rendered = []\n"
    "        rendered.append(self.nodelist.render(context))\n"
    "        return self.describe(rendered)\n"
    "\n"
    "    def describe(self, rendered):\n"
    "        return ''.join(rendered)\n"
)

_PLAIN_CLASS = (
    "class Sample:\n    def render(self, value):\n        self.seen = value\n"
)

_SHADOWED_NODE = (
    "class Node:\n"
    "    pass\n"
    "\n"
    "\n"
    "class Sample(Node):\n"
    "    def render(self, value):\n"
    "        self.seen = value\n"
)

NODE_STATE_CLEAN_CASES: tuple[NodeStateCase, ...] = (
    NodeStateCase("clean_node", (("sample.py", _CLEAN),), ()),
    NodeStateCase("plain_class", (("sample.py", _PLAIN_CLASS),), ()),
    NodeStateCase("shadowed_node", (("sample.py", _SHADOWED_NODE),), ()),
    _clean_case("alias_read", "cache = self.cache\n        context.push(cache)"),
    _clean_case("slice_copy", "items = self.items[:]\n        items.sort()"),
    _clean_case("deleted_local", "del context"),
)
