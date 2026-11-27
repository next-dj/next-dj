import contextvars
import threading

import pytest
from django.db import connection

from next.boundaries import contain, scope
from next.boundaries.calls import Addressing
from next.boundaries.failures import Boundary
from next.boundaries.ids import deferral
from next.boundaries.scope import render_boundaries
from next.transactions import savepoints
from tests.support.boundaries import RecordingErrorUI, raising


class _Minting:
    def __init__(self, minted: str | None) -> None:
        self._minted = minted

    def minted(self) -> str | None:
        return self._minted


class TestMinting:
    """The render session names the error wrappers, the render counter standing in."""

    def test_a_minted_id_names_the_wrapper(self) -> None:
        with deferral(_Minting("b-5ea1-7")):
            html = contain(
                Boundary("a", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingErrorUI(),
            )
        assert html.startswith('<div id="b-5ea1-7" data-next-boundary="b-5ea1-7"')

    def test_a_minter_without_an_id_leaves_the_render_counter(self) -> None:
        with deferral(_Minting(None)):
            html = contain(
                Boundary("a", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingErrorUI(),
            )
        assert html.startswith('<div id="b-')
        assert '-1" data-next-boundary="b-' in html


class TestRenderBoundaries:
    """One top-level render shares its state, its settings and its alias probe."""

    def test_siblings_share_one_probe_and_one_numbering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []

        def probe() -> tuple[str, ...]:
            probes.append(1)
            return ()

        monkeypatch.setattr(scope, "probe_atomic_aliases", probe)
        with render_boundaries():
            html = "".join(
                contain(
                    Boundary(name, Addressing.COMPONENT),
                    raising(ValueError()),
                    RecordingErrorUI(),
                )
                for name in ("a", "b", "c")
            )
        ids = [part.split('"')[0] for part in html.split('data-next-boundary="')[1:]]
        assert len(probes) == 1
        assert [identifier.rsplit("-", 1)[1] for identifier in ids] == ["1", "2", "3"]

    def test_a_render_without_a_boundary_opens_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []
        monkeypatch.setattr(
            scope, "probe_atomic_aliases", lambda: probes.append(1) or ()
        )
        with render_boundaries():
            pass
        assert probes == []
        assert savepoints._local.session is None

    def test_the_wrappers_stay_for_the_render_and_go_with_it(self) -> None:
        with render_boundaries():
            contain(
                Boundary("a", Addressing.COMPONENT), lambda: "ok", RecordingErrorUI()
            )
            assert savepoints._local.session is not None
            contain(
                Boundary("b", Addressing.COMPONENT), lambda: "ok", RecordingErrorUI()
            )
        assert savepoints._local.session is None
        assert connection.execute_wrappers == []

    def test_a_nested_entry_joins_the_open_render(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []
        monkeypatch.setattr(
            scope, "probe_atomic_aliases", lambda: probes.append(1) or ()
        )
        with render_boundaries():
            contain(
                Boundary("a", Addressing.COMPONENT), lambda: "ok", RecordingErrorUI()
            )
            with render_boundaries():
                contain(
                    Boundary("b", Addressing.COMPONENT),
                    lambda: "ok",
                    RecordingErrorUI(),
                )
            assert savepoints._local.session is not None
        assert len(probes) == 1

    def test_another_thread_opens_a_render_of_its_own(self) -> None:
        seen: list[str] = []

        def work() -> None:
            seen.append(
                contain(
                    Boundary("t", Addressing.COMPONENT),
                    raising(ValueError()),
                    RecordingErrorUI(),
                )
            )

        with render_boundaries():
            contain(
                Boundary("a", Addressing.COMPONENT), lambda: "ok", RecordingErrorUI()
            )
            thread = threading.Thread(
                target=contextvars.copy_context().run, args=(work,)
            )
            thread.start()
            thread.join()
            assert savepoints._local.session is not None
        assert seen[0].startswith('<div id="b-')
