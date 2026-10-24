import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.load.harness import Outcome
from tests.load.profiles import Profile


def document(
    profile: Profile, machine: Mapping[str, object], outcome: Outcome
) -> dict[str, object]:
    """Shape one run into the JSON result document."""
    published = outcome.published
    elapsed = max((report.elapsed for report in outcome.publishers), default=0.0)
    return {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": asdict(profile),
        "machine": dict(machine),
        "publish": {
            "messages": published,
            "errors": outcome.publish_errors,
            "target_per_second": profile.rate * profile.publishers,
            "throughput_per_second": round(published / elapsed, 1) if elapsed else 0.0,
        },
        "transports": {
            name: totals.as_dict() for name, totals in outcome.transports.items()
        },
        "server": outcome.server,
        "timings_seconds": {
            "open": round(outcome.open_seconds, 3),
            "publish": round(outcome.publish_seconds, 3),
            "drain": round(outcome.drain_seconds, 3),
        },
    }


def write(result: Mapping[str, object], directory: Path, profile: str) -> Path:
    """Write the result under a UTC timestamped name and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-{profile}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path


def _latency(section: Mapping[str, Any]) -> str:
    """Format one latency distribution as a compact p50/p95/p99/max line."""
    return " ".join(
        f"{label} {section[label]}" for label in ("p50", "p95", "p99", "max")
    )


def summary(profile: Profile, result: Mapping[str, Any], path: Path) -> str:
    """Return a few human lines naming the shape, delivery, latency and server load."""
    publish = result["publish"]
    server = result["server"]
    lines = [
        "",
        (
            f"next.dj realtime load, profile {profile.name}: "
            f"{profile.publishers} publishers x {profile.rate:g} msg/s over "
            f"{profile.topics} topics for {profile.duration:g} s, "
            f"{profile.sse_clients} SSE + {profile.ws_clients} WS clients in "
            f"{profile.client_processes} client process(es)"
        ),
        (
            f"publish   {publish['messages']} messages, "
            f"{publish['throughput_per_second']}/{publish['target_per_second']:g} "
            f"msg/s, {publish['errors']} errors"
        ),
    ]
    for name, section in result["transports"].items():
        latency = section["latency_ms"]
        lines.extend(
            (
                (
                    f"{name:<9} hello {section['hello']}/{section['clients']}, "
                    f"delivered {section['delivered']}/{section['expected']}, "
                    f"lost {section['lost']} (worst client {section['worst_lost_ratio']}), "
                    f"resyncs {section['resyncs']}, restarts {section['restarts']}, "
                    f"reconnects {section['reconnects']}, "
                    f"gave up {section['gave_up']}, errors {section['errors']}"
                ),
                f"{'':<9} end-to-end ms {_latency(latency['end_to_end'])}",
                f"{'':<9} server-to-client ms {_latency(latency['server_to_client'])}",
                f"{'':<9} hello ms {_latency(latency['hello'])}",
            )
        )
    lines.extend(
        (
            (
                f"server    cpu avg {server['cpu_avg']}% max {server['cpu_max']}%, "
                f"rss max {server['rss_max_mb']} MB"
            ),
            f"result    {path}",
            "",
        )
    )
    return "\n".join(lines)
