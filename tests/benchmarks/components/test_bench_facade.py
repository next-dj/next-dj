"""Benchmarks for ``next.components.facade.render_component`` signal overhead.

``render_component`` emits ``component_rendered`` after every render. These
benches quantify the Signal.send cost with and without an attached
receiver so regressions there are visible as soon as they land.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.dispatch import Signal

from next.components.info import ComponentInfo
from next.components.signals import component_rendered


if TYPE_CHECKING:
    from pathlib import Path


def _make_info(tmp_path: Path) -> ComponentInfo:
    template = tmp_path / "c.djx"
    template.write_text("<div></div>")
    return ComponentInfo(
        name="c",
        scope_root=tmp_path,
        scope_relative="",
        template_path=template,
        module_path=None,
        is_simple=True,
    )


def _noop_receiver(sender: object, **_: object) -> None:  # pragma: no cover
    del sender


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
        info = _make_info(tmp_path)
        benchmark(_send_component_rendered, component_rendered, object(), info)

    @pytest.mark.benchmark(group="components.signals")
    def test_send_with_one_receiver(self, tmp_path: Path, benchmark) -> None:
        """Cost of dispatching ``component_rendered`` to one user receiver."""
        info = _make_info(tmp_path)
        component_rendered.connect(_noop_receiver)
        try:
            benchmark(
                _send_component_rendered,
                component_rendered,
                object(),
                info,
            )
        finally:
            component_rendered.disconnect(_noop_receiver)
