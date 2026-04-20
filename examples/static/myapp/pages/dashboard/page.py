from next.deps import Depends, resolver
from next.pages import Context, context


styles = [
    "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap",
]
scripts: list[str] = []


@resolver.dependency("build_metadata")
def get_build_metadata() -> dict[str, str]:
    return {"version": "0.4", "env": "demo"}


@context
def dashboard_stats() -> dict[str, object]:
    return {
        "active_users": 42,
        "uptime_hours": 128,
        "requests_per_minute": 310,
    }


@context("build_info")
def build_info(
    build_metadata: dict[str, str] = Depends("build_metadata"),
) -> dict[str, str]:
    return build_metadata


@context("inherited_theme")
def inherited_theme(
    theme: str | None = Context("theme", default="unknown"),
) -> str | None:
    return theme
