import asyncio
import threading
from concurrent.futures import Future
from unittest.mock import MagicMock

import pytest
from asgiref.sync import sync_to_async

from next.bridge.hops import parent_loop, route_to
from next.bridge.request import RequestThreadExecutor, request_executor, serve_until
from tests.bridge.shared import expiring, interrupt, submit_elsewhere
from tests.support.errors import fail_hop
from tests.support.waits import WAIT


class TestRequestThreadExecutor:
    """Tests for ``RequestThreadExecutor``."""

    def test_a_hop_runs_on_the_serving_thread(self) -> None:
        executor = RequestThreadExecutor()
        done: Future[None] = Future()
        submitted: list[Future[int]] = []

        def submit() -> None:
            hop = executor.submit(threading.get_ident)
            hop.add_done_callback(lambda _: done.set_result(None))
            submitted.append(hop)

        thread = threading.Thread(target=submit, daemon=True)
        thread.start()
        executor.serve(done, WAIT)
        thread.join(WAIT)
        assert not thread.is_alive()
        assert submitted[0].result(0) == threading.get_ident()

    def test_a_hop_from_its_own_thread_raises(self) -> None:
        executor = RequestThreadExecutor()
        with pytest.raises(
            RuntimeError,
            match="cannot submit onto RequestThreadExecutor from its own thread",
        ):
            executor.submit(threading.get_ident)

    def test_a_wake_makes_the_server_recheck_its_future(self) -> None:
        executor = RequestThreadExecutor()
        future = MagicMock(spec=Future)
        future.done.side_effect = [False, True]
        server = threading.Thread(target=executor.serve, args=(future,), daemon=True)
        server.start()
        executor.wake()
        server.join(WAIT)
        assert not server.is_alive()

    def test_a_zero_timeout_runs_the_queued_hops_and_returns(self) -> None:
        executor = RequestThreadExecutor()
        first = submit_elsewhere(executor, threading.get_ident)
        second = submit_elsewhere(executor, threading.get_ident)
        executor.serve(Future(), 0.0)
        assert first.result(0) == second.result(0) == threading.get_ident()

    def test_a_timed_serve_returns_when_no_hop_arrives(self) -> None:
        executor = RequestThreadExecutor()
        future: Future[None] = Future()
        server = threading.Thread(target=executor.serve, args=(future, 0.01))
        server.start()
        server.join(WAIT)
        assert not server.is_alive()
        assert not future.done()

    def test_serve_returns_at_once_for_a_done_future(self) -> None:
        executor = RequestThreadExecutor()
        done: Future[None] = Future()
        done.set_result(None)
        hop = submit_elsewhere(executor, threading.get_ident)
        executor.serve(done, WAIT)
        assert not hop.done()

    def test_a_hop_taken_after_the_future_is_done_is_left_for_close(self) -> None:
        executor = RequestThreadExecutor()
        calls: list[int] = []
        hop = submit_elsewhere(executor, calls.append, 1)
        future = MagicMock(spec=Future)
        future.done.side_effect = [False, True]
        executor.serve(future, WAIT)
        assert not hop.done()
        executor.close()
        with pytest.raises(RuntimeError, match="no longer serves"):
            hop.result(0)
        assert calls == []

    def test_an_interrupt_in_a_hop_leaves_serve(self) -> None:
        executor = RequestThreadExecutor()
        hop = submit_elsewhere(executor, interrupt)
        with pytest.raises(KeyboardInterrupt):
            executor.serve(Future(), WAIT)
        error = hop.exception(0)
        assert isinstance(error, RuntimeError)
        assert isinstance(error.__cause__, KeyboardInterrupt)

    def test_an_error_in_a_hop_settles_it_and_serving_goes_on(self) -> None:
        executor = RequestThreadExecutor()
        failed = submit_elsewhere(executor, fail_hop)
        done: Future[None] = Future()
        submit_elsewhere(executor, done.set_result, None)
        executor.serve(done, WAIT)
        assert isinstance(failed.exception(0), ValueError)

    def test_close_rejects_the_hops_still_queued(self) -> None:
        executor = RequestThreadExecutor()
        calls: list[int] = []
        hop = submit_elsewhere(executor, calls.append, 1)
        executor.wake()
        executor.close()
        with pytest.raises(RuntimeError, match="no longer serves"):
            hop.result(0)
        assert calls == []

    def test_a_closed_executor_rejects_new_hops(self) -> None:
        executor = RequestThreadExecutor()
        executor.close()
        hop = submit_elsewhere(executor, threading.get_ident)
        with pytest.raises(RuntimeError, match="no longer serves"):
            hop.result(0)

    def test_the_loop_markers_a_hop_sets_are_put_back(self) -> None:
        executor = RequestThreadExecutor()
        before = parent_loop()
        done: Future[object] = Future()

        async def hop() -> object:
            route_to(executor)
            return await sync_to_async(parent_loop)()

        def elsewhere() -> None:
            done.set_result(asyncio.run(hop()))

        threading.Thread(target=elsewhere, daemon=True).start()
        executor.serve(done, WAIT)
        assert isinstance(done.result(0), asyncio.AbstractEventLoop)
        assert done.result(0) is not before
        assert parent_loop() is before


class TestRequestExecutor:
    """Tests for ``request_executor``."""

    def test_a_nested_caller_shares_the_outer_executor(self) -> None:
        with request_executor() as outer:
            with request_executor() as inner:
                assert inner is outer
            assert submit_elsewhere(outer, threading.get_ident).done() is False
        with request_executor() as fresh:
            assert fresh is not outer
        with pytest.raises(RuntimeError, match="no longer serves"):
            submit_elsewhere(outer, threading.get_ident).result(0)

    def test_a_close_from_another_thread_leaves_the_owners_local_alone(self) -> None:
        held = request_executor()
        stale = held.__enter__()
        errors: list[BaseException] = []

        def close() -> None:
            try:
                held.__exit__(None, None, None)
            except AttributeError as error:
                errors.append(error)

        closer = threading.Thread(target=close, daemon=True)
        closer.start()
        closer.join(WAIT)
        assert errors == []
        assert stale.closed is True
        future: Future[int] = Future()
        future.set_result(1)
        serve_until(future)
        with request_executor() as fresh:
            assert fresh is not stale
        with request_executor() as later:
            assert later is not fresh


class TestServeUntil:
    """Tests for ``serve_until``."""

    def test_a_thread_no_driver_serves_waits_plainly(self) -> None:
        future: Future[int] = Future()
        timer = threading.Timer(0.01, future.set_result, (7,))
        timer.start()
        with expiring(future):
            serve_until(future)
        timer.join(WAIT)
        assert future.result(0) == 7

    def test_a_served_thread_runs_its_hops_while_it_waits(self) -> None:
        future: Future[int] = Future()
        with request_executor() as executor:
            submit_elsewhere(executor, lambda: future.set_result(threading.get_ident()))
            with expiring(future):
                serve_until(future)
        assert future.result(0) == threading.get_ident()

    def test_a_wait_inside_a_hop_serves_the_hops_queued_behind_it(self) -> None:
        inner: Future[int] = Future()
        outer: Future[int] = Future()

        def waiting_hop() -> None:
            submit_elsewhere(executor, lambda: inner.set_result(1))
            with expiring(inner):
                serve_until(inner)
            outer.set_result(inner.result(0) + 1)

        with request_executor() as executor:
            submit_elsewhere(executor, waiting_hop)
            with expiring(outer):
                serve_until(outer)
        assert outer.result(0) == 2
