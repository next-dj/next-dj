"""Import helper backed by a process-wide dotted-path memo.

`import_class_cached` memoises lookups so a backend named by a settings key imports once
per process, and `NextFrameworkSettings.reload` clears the memo.
"""

from __future__ import annotations

import functools
from typing import Any, cast

from django.utils.module_loading import import_string


@functools.cache
def import_class_cached(dotted_path: str) -> type[Any]:
    """Import a class by dotted path and memoise it until the memo is cleared."""
    return cast("type[Any]", import_string(dotted_path))


# Named for what a caller drops rather than for the memo behind it.
clear_import_cache = import_class_cached.cache_clear
