from typing import Any

from access.models import AuditEntry

from next.components import component


_KIND_CLASS = {
    AuditEntry.KIND_DISPATCHED: "bg-emerald-100 text-emerald-800",
    AuditEntry.KIND_VALIDATION_FAILED: "bg-rose-100 text-rose-800",
    AuditEntry.KIND_REQUEST_STARTED: "bg-slate-100 text-slate-700",
    AuditEntry.KIND_ACCESS_DENIED: "bg-rose-100 text-rose-800",
}
_SOURCE_CLASS = {
    AuditEntry.SOURCE_BACKEND: "bg-indigo-100 text-indigo-800",
    AuditEntry.SOURCE_SIGNAL: "bg-amber-100 text-amber-800",
}


@component.context("kind_class")
def kind_class(entry: AuditEntry) -> str:
    return _KIND_CLASS.get(entry.kind, "bg-slate-100 text-slate-700")


@component.context("source_class")
def source_class(entry: AuditEntry) -> str:
    return _SOURCE_CLASS.get(entry.source, "bg-slate-100 text-slate-700")


@component.context("summary")
def summary(entry: AuditEntry) -> str:
    """Return a short human description derived from the row's metric fields."""
    parts: list[str] = []
    if entry.duration_ms is not None:
        parts.append(f"{entry.duration_ms:.1f} ms")
    if entry.error_count:
        names = ", ".join(entry.field_names) if entry.field_names else ""
        suffix = "s" if entry.error_count != 1 else ""
        joined = f" ({names})" if names else ""
        parts.append(f"{entry.error_count} error{suffix}{joined}")
    if entry.step:
        parts.append(f"step={entry.step}")
    if entry.access_layer:
        parts.append(f"{entry.access_layer}/{entry.access_reason}")
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    redirect = payload.get("redirect")
    if redirect:
        parts.append(f"→ {redirect}")
    return " · ".join(parts) or "—"


@component.context("status_label")
def status_label(entry: AuditEntry) -> str:
    return str(entry.response_status) if entry.response_status is not None else ""


@component.context("payload_keys")
def payload_keys(entry: AuditEntry) -> list[str]:
    if not isinstance(entry.payload, dict):
        return []
    return [str(k) for k in entry.payload if k != "redirect"]


@component.context("data_attrs")
def data_attrs(entry: AuditEntry) -> dict[str, Any]:
    return {"source": entry.source, "kind": entry.kind}


@component.context("request_link")
def request_link(entry: AuditEntry) -> str:
    """Return the per-request audit URL when the row is correlated."""
    if entry.request_id is None:
        return ""
    return f"/request/{entry.request_id}/audit/"
