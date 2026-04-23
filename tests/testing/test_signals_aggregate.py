from next import signals as aggregate_signals


class TestAggregateSignalsModule:
    """next.signals re-exports every signal from the subsystems."""

    def test_exports_every_signal(self) -> None:
        expected = {
            "action_dispatched",
            "action_registered",
            "asset_registered",
            "backend_loaded",
            "collector_finalized",
            "component_backend_loaded",
            "component_registered",
            "component_rendered",
            "context_registered",
            "form_validation_failed",
            "html_injected",
            "page_rendered",
            "provider_registered",
            "route_registered",
            "router_reloaded",
            "settings_reloaded",
            "template_loaded",
            "watch_specs_ready",
        }
        exported = set(aggregate_signals.__all__)
        assert expected == exported
        for name in expected:
            assert hasattr(aggregate_signals, name)
