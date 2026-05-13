from __future__ import annotations

from django.test import override_settings

from next.static import StaticManager
from next.static.collector import (
    DeepMergePolicy,
    FirstWinsPolicy,
    HashContentDedup,
    LastWinsPolicy,
    UrlDedup,
)


CONFIG_HASH_DEEP = {
    "NEXT_FRAMEWORK": {
        "DEFAULT_STATIC_BACKENDS": [
            {
                "BACKEND": "next.static.StaticFilesBackend",
                "OPTIONS": {
                    "DEDUP_STRATEGY": "next.static.collector.HashContentDedup",
                    "JS_CONTEXT_POLICY": "next.static.collector.DeepMergePolicy",
                },
            },
        ],
    },
}

CONFIG_LAST_WINS_ONLY = {
    "NEXT_FRAMEWORK": {
        "DEFAULT_STATIC_BACKENDS": [
            {
                "BACKEND": "next.static.StaticFilesBackend",
                "OPTIONS": {
                    "JS_CONTEXT_POLICY": "next.static.collector.LastWinsPolicy",
                },
            },
        ],
    },
}


class TestCreateCollectorDefaults:
    """Without OPTIONS the collector uses the built-in defaults."""

    def test_default_dedup_is_url_dedup(self) -> None:
        manager = StaticManager()
        collector = manager.create_collector()
        assert isinstance(collector._dedup, UrlDedup)

    def test_default_policy_is_first_wins(self) -> None:
        manager = StaticManager()
        collector = manager.create_collector()
        assert isinstance(collector._js_policy, FirstWinsPolicy)


class TestCreateCollectorWithOptions:
    """With OPTIONS the collector uses the configured strategies."""

    @override_settings(**CONFIG_HASH_DEEP)
    def test_hash_dedup_is_wired(self) -> None:
        manager = StaticManager()
        collector = manager.create_collector()
        assert isinstance(collector._dedup, HashContentDedup)

    @override_settings(**CONFIG_HASH_DEEP)
    def test_deep_merge_policy_is_wired(self) -> None:
        manager = StaticManager()
        collector = manager.create_collector()
        assert isinstance(collector._js_policy, DeepMergePolicy)

    @override_settings(**CONFIG_LAST_WINS_ONLY)
    def test_partial_options_keep_other_default(self) -> None:
        manager = StaticManager()
        collector = manager.create_collector()
        assert isinstance(collector._js_policy, LastWinsPolicy)
        assert isinstance(collector._dedup, UrlDedup)

    @override_settings(**CONFIG_HASH_DEEP)
    def test_every_call_returns_a_fresh_collector(self) -> None:
        manager = StaticManager()
        a = manager.create_collector()
        b = manager.create_collector()
        assert a is not b
        assert a._dedup is not b._dedup
