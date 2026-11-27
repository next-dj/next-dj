"""Write `next/client/protocol.gen.ts` from the Python constants of the wire protocol.

The constants are read from the source with `ast`, so the build needs neither Django
nor an installed package, and the file runs as a script from `next/client/build.mjs`.
"""

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
"""The checkout the sources and the generated module are read from and written to."""

TARGET = "next/client/protocol.gen.ts"
"""Where the generated module lives, relative to `ROOT`."""

_HEADERS = "next/partial/headers.py"
_KEYS = "next/partial/keys.py"
_STREAM = "next/partial/stream.py"
_WIRE = "next/wire.py"
_RUNTIME = "next/static/runtime.py"

EXPORTS: tuple[tuple[str, str, str], ...] = (
    ("CONTENT_TYPE", _HEADERS, "CONTENT_TYPE"),
    ("ACCEPT", _HEADERS, "ACCEPT"),
    ("STREAM_ACCEPT", _HEADERS, "STREAM_ACCEPT"),
    ("EVENT_STREAM", _HEADERS, "EVENT_STREAM"),
    ("REQUEST_FLAG", _WIRE, "REQUEST_FLAG"),
    ("HEADER_ZONE", _HEADERS, "ZONE"),
    ("HEADER_VALIDATE", _HEADERS, "VALIDATE"),
    ("HEADER_MERGE", _HEADERS, "MERGE"),
    ("HEADER_VERSION", _HEADERS, "VERSION"),
    ("HEADER_PROTOCOL", _HEADERS, "PROTOCOL"),
    ("HEADER_REQUEST_ID", _HEADERS, "REQUEST_ID"),
    ("HEADER_ORIGIN", _HEADERS, "ORIGIN"),
    ("HEADER_BOUNDARY", _HEADERS, "BOUNDARY"),
    ("HEADER_NAVIGATE", _HEADERS, "NAVIGATE"),
    ("HEADER_LAYOUTS", _HEADERS, "LAYOUTS"),
    ("HEADER_PREFETCH", _HEADERS, "PREFETCH"),
    ("HEADER_ROUTER", _HEADERS, "ROUTER"),
    ("HEADER_TRANSPORT", "next/forms/transport.py", "TRANSPORT_HEADER"),
    ("PREFETCH_SHELL", _HEADERS, "PREFETCH_SHELL"),
    ("PREFETCH_FULL", _HEADERS, "PREFETCH_FULL"),
    ("MAX_LAYOUTS", "next/router/markers.py", "MAX_LAYOUTS"),
    ("ROUTE_PAGE", "next/urls/scope.py", "PAGE_ROW"),
    ("ROUTER_CLEAR", "next/router/navigation/clearing.py", "CLEAR"),
    ("ROUTER_NO_SNAPSHOT", "next/router/navigation/clearing.py", "NO_SNAPSHOT"),
    ("ROUTER_OFF", "next/router/speculation.py", "OFF"),
    ("PROTOCOL", _STREAM, "PROTOCOL"),
    ("EVENT_HELLO", _STREAM, "HELLO_EVENT"),
    ("EVENT_PATCH", _STREAM, "PATCH_EVENT"),
    ("EVENT_END", _STREAM, "END_EVENT"),
    ("SSE_HELLO", _KEYS, "SSE_HELLO"),
    ("SSE_SKIP", _KEYS, "SSE_SKIP"),
    ("SSE_END", _KEYS, "SSE_END"),
    ("SSE_PATCHES", _KEYS, "SSE_PATCHES"),
    ("TOPIC", _KEYS, "TOPIC"),
    ("SEQ", _KEYS, "SEQ"),
    ("EPOCH", _KEYS, "EPOCH"),
    ("SERVER_TIME", _KEYS, "SERVER_TIME"),
    ("CHUNK_BOUNDARY", _KEYS, "CHUNK_BOUNDARY"),
    ("CHUNK_STATE", _KEYS, "CHUNK_STATE"),
    ("CHUNK_KIND", _KEYS, "CHUNK_KIND"),
    ("CHUNK_PARENT", _KEYS, "CHUNK_PARENT"),
    ("CHUNK_ENVELOPE", _KEYS, "CHUNK_ENVELOPE"),
    ("CHUNK_DIGEST", _KEYS, "CHUNK_DIGEST"),
    ("CHUNK_TEMPLATE", _KEYS, "CHUNK_TEMPLATE"),
    ("CHUNK_END", _KEYS, "CHUNK_END"),
    ("NOSCRIPT_PARAM", _KEYS, "NOSCRIPT_PARAM"),
    ("RESTORE_PARAM", "next/partial/paging.py", "RESTORE_PARAM"),
    ("LESS_SUFFIX", "next/partial/more.py", "LESS_SUFFIX"),
    ("TOKEN_FIELD", "next/defer/tokens/carrier.py", "FIELD"),
    ("ORIGIN_FIELD", "next/forms/uid.py", "ORIGIN_FIELD_NAME"),
    ("SLOT_MARK", "next/pages/layouts.py", "SLOT"),
    ("REFUSAL_DISABLED", "next/realtime/frames.py", "Refusal.DISABLED"),
    ("REFUSAL_STALE", "next/realtime/frames.py", "Refusal.STALE"),
    ("REFUSAL_HTTP_ONLY", "next/realtime/frames.py", "Refusal.HTTP_ONLY"),
    ("PAYLOAD_CSRF", _RUNTIME, "CSRF_PAYLOAD_KEY"),
    ("PAYLOAD_DEV", _RUNTIME, "DEV_PAYLOAD_KEY"),
    ("PAYLOAD_CHUNKS", _RUNTIME, "CHUNKS_PAYLOAD_KEY"),
    ("PAYLOAD_REALTIME", _RUNTIME, "REALTIME_PAYLOAD_KEY"),
    ("PAYLOAD_SCRIPTS", _RUNTIME, "SCRIPTS_PAYLOAD_KEY"),
    ("PAYLOAD_CONSENT", _RUNTIME, "CONSENT_PAYLOAD_KEY"),
    ("PAYLOAD_STREAM", _RUNTIME, "STREAM_PAYLOAD_KEY"),
    ("PAYLOAD_VERSION", _RUNTIME, "VERSION_PAYLOAD_KEY"),
    ("PAYLOAD_PROTOCOL", _RUNTIME, "PROTOCOL_PAYLOAD_KEY"),
    ("PAYLOAD_ROUTER", _RUNTIME, "ROUTER_PAYLOAD_KEY"),
    ("ATTR_INIT", _RUNTIME, "INIT_ATTR"),
    ("ATTR_LAYOUTS", "next/router/render.py", "LAYOUTS_ATTR"),
    ("ATTR_ZONE", "next/partial/zone.py", "ZONE_ATTR"),
    ("ATTR_POLL", "next/partial/zone.py", "POLL_ATTR"),
    ("ATTR_RELOAD", "next/partial/zone.py", "RELOAD_ATTR"),
    ("ATTR_DELAY", "next/partial/zone.py", "DELAY_ATTR"),
    ("ATTR_KEY", _WIRE, "KEY_ATTR"),
    ("ATTR_TARGET", _KEYS, "TARGET_ATTR"),
    ("ATTR_TRANSPORT", "next/forms/transport.py", "TRANSPORT_ATTR"),
    ("ATTR_LAYER", "next/partial/layers.py", "LAYER_ATTR"),
    ("ATTR_RETRY", "next/boundaries/markup.py", "RETRY_ATTR"),
    ("ATTR_SCRIPT", "next/scripts/markers.py", "SCRIPT_ATTR"),
    ("ATTR_MERGE", "next/partial/more.py", "MERGE_ATTR"),
    ("ATTR_MARGIN", "next/partial/more.py", "MARGIN_ATTR"),
    ("ATTR_DEFER", "next/partial/more.py", "DEFER_ATTR"),
    ("ATTR_PARAM", "next/partial/more.py", "PARAM_ATTR"),
    ("ATTR_PRUNE", "next/partial/more.py", "PRUNE_ATTR"),
    ("ATTR_URL", "next/partial/more.py", "URL_ATTR"),
    ("ATTR_BACK", "next/partial/more.py", "BACK_ATTR"),
    ("ATTR_BACKS", "next/partial/more.py", "BACKS_ATTR"),
    ("ATTR_STATE", _WIRE, "STATE_ATTR"),
    ("ATTR_ERROR", _WIRE, "ERROR_ATTR"),
    ("ATTR_DIGEST", _WIRE, "DIGEST_ATTR"),
    ("ATTR_BOUNDARY", _WIRE, "BOUNDARY_ATTR"),
    ("ATTR_STREAMED", _KEYS, "STREAMED_ATTR"),
    ("ATTR_ASSET", _WIRE, "ASSET_ATTR"),
    ("ATTR_TOKEN", _KEYS, "TRIP_ATTR"),
    ("ATTR_CARRIER", _KEYS, "CARRIER_ATTR"),
    ("ATTR_ADOPTED", _KEYS, "ADOPTED_ATTR"),
    ("ATTR_ACTION", _WIRE, "ACTION_ATTR"),
    ("ATTR_BOUNDARY_KEY", _WIRE, "BOUNDARY_KEY_ATTR"),
    ("ATTR_DETAIL", _WIRE, "DETAIL_ATTR"),
    ("ATTR_CURRENT", _WIRE, "CURRENT_ATTR"),
    ("ATTR_MATCH", _KEYS, "MATCH_ATTR"),
    ("ATTR_FOCUS", _KEYS, "FOCUS_ATTR"),
    ("ATTR_PERSIST", _KEYS, "PERSIST_ATTR"),
    ("ATTR_PREFETCH", _KEYS, "PREFETCH_ATTR"),
    ("ATTR_PRESS", _KEYS, "PRESS_ATTR"),
    ("ATTR_ROUTER", _KEYS, "ROUTER_ATTR"),
    ("ATTR_SCROLL", _KEYS, "SCROLL_ATTR"),
    ("ATTR_SCROLL_REGION", _KEYS, "SCROLL_REGION_ATTR"),
    ("ATTR_TOASTS", _KEYS, "TOASTS_ATTR"),
    ("ATTR_RESERVE", _KEYS, "RESERVE_ATTR"),
    ("ATTR_FETCH", _KEYS, "FETCH_ATTR"),
    ("ATTR_CONFIRM", _KEYS, "CONFIRM_ATTR"),
    ("ATTR_CONSENTED", _KEYS, "CONSENTED_ATTR"),
    ("ATTR_VALIDATE", _KEYS, "VALIDATE_ATTR"),
    ("ATTR_TRIGGER", _KEYS, "TRIGGER_ATTR"),
    ("ATTR_DEBOUNCE", _KEYS, "DEBOUNCE_ATTR"),
    ("ATTR_LOADING", "next/boundaries/stash.py", "STASH_ATTR"),
)
"""Each export of the generated module, its TypeScript name and its Python source.

A source name is a module constant, or `Class.MEMBER` of an enumeration there.
"""

_WIDTH = 88
_HEAD = (
    "// Generated by next/static/protocol.py from the Python constants of the wire\n"
    "// protocol. Edit the Python source and run the build, never this file.\n"
)


class _Reader:
    """Read module constants from source, following imports and f-strings of names."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._indexed: dict[
            str, tuple[dict[str, ast.expr], dict[str, tuple[str, str]]]
        ] = {}

    def _module_file(self, dotted: str) -> str:
        base = dotted.replace(".", "/")
        return (
            f"{base}.py"
            if (self.root / f"{base}.py").exists()
            else f"{base}/__init__.py"
        )

    def _index(
        self, source: str
    ) -> tuple[dict[str, ast.expr], dict[str, tuple[str, str]]]:
        if source not in self._indexed:
            text = (self.root / source).read_text(encoding="utf-8")
            package = source.removesuffix(".py").split("/")[:-1]
            self._indexed[source] = self._scan(ast.parse(text).body, package)
        return self._indexed[source]

    def _scan(
        self, body: list[ast.stmt], package: list[str]
    ) -> tuple[dict[str, ast.expr], dict[str, tuple[str, str]]]:
        defs: dict[str, ast.expr] = {}
        imports: dict[str, tuple[str, str]] = {}
        for node in body:
            if isinstance(node, ast.ClassDef):
                members, _ = self._scan(node.body, package)
                defs.update(
                    {f"{node.name}.{key}": item for key, item in members.items()}
                )
            elif isinstance(node, ast.ImportFrom):
                base = package[: len(package) - (node.level - 1)] if node.level else []
                dotted = ".".join(
                    [*base, *(node.module.split(".") if node.module else [])]
                )
                for alias in node.names:
                    imports[alias.asname or alias.name] = (
                        self._module_file(dotted),
                        alias.name,
                    )
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
            ):
                defs[node.target.id] = node.value
            elif isinstance(node, ast.Assign):
                defs.update(
                    {
                        target.id: node.value
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    }
                )
        return defs, imports

    def value(self, source: str, name: str) -> object:
        """Return the value of constant `name` of the module file `source`."""
        defs, imports = self._index(source)
        if name in defs:
            return self._evaluate(source, defs[name])
        origin, original = imports[name]
        return self.value(origin, original)

    def _evaluate(self, source: str, node: ast.expr) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(str(self._evaluate(source, part)) for part in node.values)
        if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name):
            return self.value(source, node.value.id)
        msg = f"{source} line {node.lineno} is not a constant the protocol reads"
        raise ValueError(msg)


def values(root: Path = ROOT) -> dict[str, object]:
    """Return the value of every export, read from its Python source under `root`."""
    reader = _Reader(root)
    return {name: reader.value(source, constant) for name, source, constant in EXPORTS}


def _line(name: str, value: object) -> str:
    """Return one export as Prettier lays it out, a long value on its own line."""
    line = f"export const {name} = {json.dumps(value)};"
    if len(line) <= _WIDTH:
        return line
    return f"export const {name} =\n  {json.dumps(value)};"


def render(root: Path = ROOT) -> str:
    """Return the text of the generated module."""
    lines = [_line(name, value) for name, value in values(root).items()]
    return _HEAD + "\n" + "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    """Write the module, or with `--check` report whether the written one is current.

    A path among the arguments stands in for `TARGET`.
    """
    named = [arg for arg in argv if not arg.startswith("--")]
    target = ROOT / (named[0] if named else TARGET)
    text = render()
    current = target.read_text(encoding="utf-8") if target.exists() else None
    if "--check" in argv:
        return int(current != text)
    if current != text:
        target.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
