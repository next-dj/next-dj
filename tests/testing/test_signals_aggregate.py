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
            "components_registered",
            "context_registered",
            "field_validated",
            "form_access_denied",
            "form_validation_failed",
            "html_injected",
            "page_rendered",
            "patch_op_registered",
            "provider_registered",
            "route_registered",
            "router_reloaded",
            "settings_reloaded",
            "sse_stream_closed",
            "sse_stream_opened",
            "template_loaded",
            "watch_specs_ready",
            "wizard_completed",
            "wizard_step_submitted",
            "zone_registered",
            "zone_rendered",
        }
        exported = set(aggregate_signals.__all__)
        assert expected == exported
        for name in expected:
            assert hasattr(aggregate_signals, name)
