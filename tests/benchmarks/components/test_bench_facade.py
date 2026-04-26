"""Benchmarks for ``next.components.facade.render_component`` signal overhead.

``render_component`` emits ``component_rendered`` after every render. These
benches quantify the Signal.send cost with and without an attached
receiver so regressions there are visible as soon as they land.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.components.signals import component_rendered
from tests.benchmarks.factories import build_component_info, noop_signal_receiver


if TYPE_CHECKING:
    from pathlib import Path

    from django.dispatch import Signal

    from next.components.info import ComponentInfo


def _send_component_rendered(
    signal: Signal,
    sender: object,
    info: ComponentInfo,
) -> None:
    signal.send(sender=sender, info=info, template_path=info.template_path)


class TestBenchComponentRenderedSignal:
    @pytest.mark.benchmark(group="components.signals")
    def test_send_no_receiver(self, tmp_path: Path, benchmark) -> None:
        """Baseline: ``component_rendered.send`` with zero receivers."""
        info = build_component_info(tmp_path)
        benchmark(_send_component_rendered, component_rendered, object(), info)

    @pytest.mark.benchmark(group="components.signals")
    def test_send_with_one_receiver(self, tmp_path: Path, benchmark) -> None:
        """Cost of dispatching ``component_rendered`` to one user receiver."""
        info = build_component_info(tmp_path)
        component_rendered.connect(noop_signal_receiver)
        try:
            benchmark(
                _send_component_rendered,
                component_rendered,
                object(),
                info,
            )
        finally:
            component_rendered.disconnect(noop_signal_receiver)
