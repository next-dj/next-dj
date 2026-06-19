import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from next.partial import Envelope, PartialProtocolBackend, PatchResponse
from next.partial.headers import CONTENT_TYPE


GOLDEN_DIR = Path(__file__).parent / "golden"


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One golden fixture pairing an envelope with its response headers."""

    name: str
    envelope: Envelope
    description: str
    status: int = 200
    version: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes through a pid-unique temp and an atomic replace.

    The golden fixtures are shared files, and the writer tests are
    parametrised across xdist workers that rewrite the same case in
    parallel. A pid-suffixed temp keeps the workers from clobbering one
    another, and the replace lets a concurrent reader see a whole file
    rather than a truncated mid-write one.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_case(case: GoldenCase) -> tuple[Path, Path]:
    """Write the envelope bytes and a metadata sidecar for one case.

    The `<name>.envelope.json` file is the exact byte body the protocol
    backend emits, so vitest reads the same bytes the server sends. The
    `<name>.meta.json` sidecar carries the content type and the response
    headers vitest needs to classify the response.
    """
    backend = PartialProtocolBackend()
    body = backend.serialize_envelope(case.envelope)
    response = PatchResponse(
        body,
        content_type=backend.content_type,
        version=case.version,
        status=case.status,
    )
    for name, value in case.extra_headers.items():
        response[name] = value
    headers = dict(response.items())

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    envelope_path = GOLDEN_DIR / f"{case.name}.envelope.json"
    meta_path = GOLDEN_DIR / f"{case.name}.meta.json"
    _atomic_write_bytes(envelope_path, body)
    meta = {
        "name": case.name,
        "description": case.description,
        "content_type": backend.content_type,
        "status": case.status,
        "headers": headers,
        "envelope_file": envelope_path.name,
    }
    meta_text = json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_bytes(meta_path, meta_text.encode("utf-8"))
    return envelope_path, meta_path


def read_envelope_bytes(name: str) -> bytes:
    """Return the raw envelope bytes written for the named case."""
    return (GOLDEN_DIR / f"{name}.envelope.json").read_bytes()


def read_meta(name: str) -> dict[str, object]:
    """Return the metadata sidecar written for the named case."""
    return json.loads((GOLDEN_DIR / f"{name}.meta.json").read_text(encoding="utf-8"))


CONTENT_TYPE_GOLDEN = CONTENT_TYPE
