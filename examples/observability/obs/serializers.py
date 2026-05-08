"""Pydantic-aware JS context serializer used by the dashboard.

The framework already ships a `PydanticJsContextSerializer` in
`next.static.serializers`. The example re-exports it under the same
name from inside the app so the configuration string in
`config/settings.py` points at example code, not framework code. This
lets the example demonstrate the public extension point. The class
below is a thin subclass with no behaviour change.
"""

from next.static import (
    PydanticJsContextSerializer as _FrameworkPydantic,
)


class PydanticJsContextSerializer(_FrameworkPydantic):
    """Subclass kept for clarity in `JS_CONTEXT_SERIALIZER` settings."""
