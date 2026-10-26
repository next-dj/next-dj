"""Registry of the upstream handlers a client event on a granted topic reaches."""

import functools
import os
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from importlib import import_module
from types import CodeType, MappingProxyType
from typing import Final, cast

from asgiref.sync import iscoroutinefunction
from django.utils.module_loading import autodiscover_modules

from next.conf import next_framework_settings

from .access import run_pooled
from .signals import areceived


type Handler = Callable[[Upstream], Awaitable[None] | None]

MAX_PATTERN_LENGTH: Final = 200
MAX_NAME_LENGTH: Final = 100

_SPLIT = re.compile(r"\{([^{}]*)\}")
_LITERAL = re.compile(r"[A-Za-z0-9_.:-]*")
_SEPARATOR = re.compile(r"[.:]")
_PLACEHOLDER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SEGMENT = "[A-Za-z0-9_-]+"
_NAME = re.compile(rf"[A-Za-z0-9_.:-]{{1,{MAX_NAME_LENGTH}}}")
_RESERVED_PREFIX = "next:"
_NO_PARAMS: Final[Mapping[str, str]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Upstream:
    """One client event on a granted topic, with the connection that sent it.

    `subject` is the opaque `Handshake.subject`, never the credential behind it.
    """

    topic: str
    name: str
    data: object
    user: object
    subject: str
    connection: str
    params: Mapping[str, str] = _NO_PARAMS


def compile_pattern(pattern: object) -> re.Pattern[str] | None:
    """Return the regex a topic pattern matches with, None for an invalid pattern.

    A placeholder matches one segment, so a colon or a dot has to follow it.
    """
    if (
        not isinstance(pattern, str)
        or not 0 < len(pattern) <= MAX_PATTERN_LENGTH
        or pattern.startswith(_RESERVED_PREFIX)
    ):
        return None
    parts = _SPLIT.split(pattern)
    literals, names = parts[::2], parts[1::2]
    if not all(_LITERAL.fullmatch(literal) for literal in literals):
        return None
    if not all(_PLACEHOLDER.fullmatch(name) for name in names):
        return None
    if len(set(names)) != len(names):
        return None
    if not all(_SEPARATOR.search(literal) for literal in literals[1:-1]):
        return None
    regex = "".join(
        re.escape(literal) if index % 2 == 0 else f"(?P<{literal}>{_SEGMENT})"
        for index, literal in enumerate(parts)
    )
    return re.compile(regex)


def valid_name(name: object) -> bool:
    """Whether an event name is 1 to `MAX_NAME_LENGTH` topic characters."""
    return isinstance(name, str) and _NAME.fullmatch(name) is not None


@functools.cache
def handler_pool() -> ThreadPoolExecutor:
    """Return the pool sync handlers run on, apart from the one Django shares.

    A slow handler then fills only its own threads, sized like the default pool.
    """
    workers = min(32, (os.cpu_count() or 1) + 4)
    return ThreadPoolExecutor(workers, thread_name_prefix="next-handler")


os.register_at_fork(after_in_child=handler_pool.cache_clear)


def _runner(handler: Handler) -> Callable[[Upstream], Awaitable[None]]:
    """Return an awaitable call of the handler, a sync one on the handler pool."""
    if iscoroutinefunction(handler):
        return cast("Callable[[Upstream], Awaitable[None]]", handler)
    blocking = cast("Callable[[Upstream], None]", handler)

    async def run(upstream: Upstream) -> None:
        await run_pooled(handler_pool(), blocking, upstream)

    return run


@dataclass(frozen=True, slots=True)
class Binding:
    """One handler bound to a topic pattern and an event name."""

    pattern: str
    name: str
    handler: Handler
    matcher: re.Pattern[str] | None
    run: Callable[[Upstream], Awaitable[None]]

    @classmethod
    def bind(cls, pattern: str, name: str, handler: Handler) -> "Binding":
        """Bind a handler, with no matcher when the pattern or the name is invalid."""
        matcher = compile_pattern(pattern) if valid_name(name) else None
        return cls(pattern, name, handler, matcher, _runner(handler))

    @property
    def valid(self) -> bool:
        """Whether the pattern and the name both parse, so the binding can match."""
        return self.matcher is not None

    @property
    def placeholders(self) -> int:
        """How many placeholders the pattern holds, fewer is more specific."""
        return 0 if self.matcher is None else self.matcher.groups

    def match(self, topic: str, name: str) -> Mapping[str, str] | None:
        """Return the placeholder values for a topic and name it serves, else None."""
        if self.matcher is None or name != self.name:
            return None
        found = self.matcher.fullmatch(topic)
        if found is None:
            return None
        if not self.matcher.groups:
            return _NO_PARAMS
        return MappingProxyType(found.groupdict())

    async def __call__(self, upstream: Upstream) -> None:
        """Run the handler, a sync one on the handler pool."""
        await self.run(upstream)


def _origin(handler: Handler) -> tuple[str, str] | None:
    """Return the file and the qualified name a function is defined under."""
    code = getattr(handler, "__code__", None)
    if not isinstance(code, CodeType):
        return None
    return code.co_filename, getattr(handler, "__qualname__", "")


def _redefined(held: Handler, handler: Handler) -> bool:
    """Whether `handler` is `held` defined again, by the same file and name."""
    origin = _origin(held)
    return origin is not None and origin == _origin(handler)


class HandlerRegistry:
    """Hold one binding per topic pattern and event name, in registration order.

    A second handler under a taken pair is kept aside for the duplicate check.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._bindings: list[Binding] = []
        self._index: dict[tuple[str, str], Binding] = {}
        self._duplicates: dict[tuple[str, str], Binding] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every change, a token for derived memos."""
        return self._version

    def add(self, entry: Binding) -> Binding:
        """Register a binding, the same function defined again replacing its pair."""
        key = (entry.pattern, entry.name)
        held = self._index.get(key)
        if held is None:
            self._bindings.append(entry)
        elif held.handler is entry.handler or _redefined(held.handler, entry.handler):
            self._bindings[self._bindings.index(held)] = entry
        else:
            self._duplicates[key] = entry
            return held
        self._index[key] = entry
        self._version += 1
        return entry

    def resolve(
        self, topic: str, name: str
    ) -> tuple[Binding, Mapping[str, str]] | None:
        """Return the binding serving an event and its placeholder values, or None.

        The pattern with the fewest placeholders wins, then the one registered first.
        """
        best: tuple[Binding, Mapping[str, str]] | None = None
        for binding in self._bindings:
            params = binding.match(topic, name)
            if params is None:
                continue
            if best is None or binding.placeholders < best[0].placeholders:
                best = (binding, params)
        return best

    def duplicates(self) -> tuple[tuple[Binding, Binding], ...]:
        """Return each held binding with the other handler claiming its pair."""
        duplicates = self._duplicates.items()
        return tuple((self._index[key], other) for key, other in duplicates)

    def __iter__(self) -> Iterator[Binding]:
        """Yield every binding in registration order."""
        return iter(tuple(self._bindings))

    def __len__(self) -> int:
        """Return how many bindings are registered."""
        return len(self._bindings)

    def reset(self) -> None:
        """Drop every binding and every recorded duplicate."""
        self._bindings.clear()
        self._index.clear()
        self._duplicates.clear()
        self._version += 1


handler_registry = HandlerRegistry()


def on[H: Handler](pattern: str, name: str) -> Callable[[H], H]:
    """Register a sync or async function for the `name` events on matching topics.

    `{placeholder}` segments in `pattern` reach the handler as `Upstream.params`.
    """

    def decorator(handler: H) -> H:
        handler_registry.add(Binding.bind(pattern, name, handler))
        return handler

    return decorator


async def dispatch(upstream: Upstream) -> bool:
    """Run the handler of one upstream event, False when no handler serves it.

    The caller checks the topic grant first, and a handler error propagates to it.
    """
    found = handler_registry.resolve(upstream.topic, upstream.name)
    if found is None:
        return False
    bound, params = found
    if params:
        upstream = replace(upstream, params=params)
    await areceived(bound.handler, upstream)
    await bound(upstream)
    return True


def handler_modules() -> Sequence[str] | None:
    """Return the modules `REALTIME['HANDLERS']` lists, None to discover every app's.

    A value that is no list of module names imports nothing and fails its check.
    """
    modules = next_framework_settings.REALTIME.get("HANDLERS")
    if modules is None:
        return None
    if valid_modules(modules):
        return tuple(modules)
    return ()


def valid_modules(modules: object) -> bool:
    """Whether a `HANDLERS` value is a list or a tuple of dotted module names."""
    return isinstance(modules, list | tuple) and all(
        isinstance(module, str) and all(map(str.isidentifier, module.split(".")))
        for module in modules
    )


def autodiscover_handlers() -> None:
    """Import the handler modules, the `realtime` module of every app by default."""
    modules = handler_modules()
    if modules is None:
        autodiscover_modules("realtime")
        return
    for module in modules:
        import_module(module)


__all__ = [
    "MAX_NAME_LENGTH",
    "MAX_PATTERN_LENGTH",
    "Binding",
    "Handler",
    "HandlerRegistry",
    "Upstream",
    "autodiscover_handlers",
    "compile_pattern",
    "dispatch",
    "handler_modules",
    "handler_pool",
    "handler_registry",
    "on",
    "valid_modules",
    "valid_name",
]
