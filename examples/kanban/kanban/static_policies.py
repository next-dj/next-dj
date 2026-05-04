from __future__ import annotations

from next.static.collector import (
    DeepMergePolicy as _DeepMergePolicy,
    HashContentDedup as _HashContentDedup,
)


class HashContentDedup(_HashContentDedup):
    """Local re-export so settings reference kanban.static_policies."""


class DeepMergePolicy(_DeepMergePolicy):
    """Local re-export so settings reference kanban.static_policies."""
