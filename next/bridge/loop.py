"""The loop a coroutine of sync code runs on, the server one or the background one."""

import asyncio
import atexit
import logging
import threading
from collections.abc import Callable
from typing import Any

from django.conf import settings

from next.caches import FORK

from .hops import parent_loop, parent_tasks
from .tasks import contextless


logger = logging.getLogger("next.bridge")

_THREAD_NAME = "next-background-loop"
_STOPPED = "The background loop stopped before the coroutine finished."


def _report(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """Log what a task or a callback of the background loop let out, as asyncio would.

    The loop belongs to the framework, so its reports go to the package logger.
    """
    message = str(context.get("message") or "Unhandled exception in the loop.")
    details = [
        f"{key}: {value!r}"
        for key, value in sorted(context.items())
        if key not in {"message", "exception"}
    ]
    logger.error(
        "%s", "\n".join((message, *details)), exc_info=context.get("exception")
    )


def _run(loop: asyncio.AbstractEventLoop, done: Callable[[], bool]) -> None:
    """Run `loop` until a stop finds `done` true, reporting interrupts a task lets out.

    A task re-raises `KeyboardInterrupt` and `SystemExit` out of the loop it runs on.
    """
    while True:
        try:
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit) as error:
            loop.call_exception_handler(
                {
                    "message": "A task raised an interrupt on the loop.",
                    "exception": error,
                }
            )
        if done():
            return


class _LoopThread:
    """One event loop and the daemon thread that runs it until halted."""

    def __init__(self) -> None:
        """Create the loop and start its thread in an empty context, which pins nothing.

        The loop handles and a thread that inherits a context keep it for the process.
        """
        self.loop = contextless(asyncio.new_event_loop)
        if settings.DEBUG:
            self.loop.set_debug(enabled=True)
            self.loop.slow_callback_duration = 0.1
        self.loop.set_exception_handler(_report)
        self.halted = False
        self.thread = threading.Thread(
            target=self._serve, name=_THREAD_NAME, daemon=True
        )
        contextless(self.thread.start)

    @property
    def serving(self) -> bool:
        """Whether the thread still runs the loop."""
        return self.thread.is_alive() and not self.loop.is_closed()

    def halt(self) -> None:
        """Stop the loop after every callback scheduled before this call."""
        self.loop.call_soon_threadsafe(self._halt)

    def _halt(self) -> None:
        self.halted = True
        self.loop.stop()

    def _serve(self) -> None:
        """Run the loop until halted, then cancel and settle its tasks and close it."""
        _run(self.loop, lambda: self.halted)
        tasks = asyncio.all_tasks(self.loop)
        for task in tasks:
            task.cancel(_STOPPED)
        if tasks:
            gathered = asyncio.gather(*tasks, return_exceptions=True)
            gathered.add_done_callback(lambda _: self.loop.stop())
            _run(self.loop, gathered.done)
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()


class BackgroundLoop:
    """One daemon thread running one event loop, started by the first caller.

    Callers outside a server loop run coroutines here instead of a loop per request.
    """

    def __init__(self) -> None:
        """Hold no loop and no thread until `get` is called."""
        self.forget()

    def forget(self) -> None:
        """Drop the loop, its thread and the lock, as a forked child must."""
        self._lock = threading.Lock()
        self._running: _LoopThread | None = None

    def current(self) -> asyncio.AbstractEventLoop | None:
        """Return the running loop without starting one."""
        running = self._running
        return None if running is None else running.loop

    def _ensure(self) -> _LoopThread:
        if self._running is None or not self._running.serving:
            self._running = _LoopThread()
        return self._running

    def get(self) -> asyncio.AbstractEventLoop:
        """Return the loop, starting a thread when none serves one."""
        with self._lock:
            return self._ensure().loop

    def call_soon[*Ts](
        self, callback: Callable[[*Ts], object], *args: *Ts
    ) -> asyncio.AbstractEventLoop:
        """Schedule `callback` on the loop and return the loop.

        Holding the lock orders it ahead of a concurrent `stop`, which settles it.
        """
        with self._lock:
            loop = self._ensure().loop
            loop.call_soon_threadsafe(callback, *args)
        return loop

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel the loop's tasks, let them settle, close it and join its thread."""
        with self._lock:
            running, self._running = self._running, None
            if running is None or not running.serving:
                return
            running.halt()
        running.thread.join(timeout)


background_loop = BackgroundLoop()
FORK.attach(background_loop.forget)
atexit.register(background_loop.stop)


def running_loop() -> asyncio.AbstractEventLoop | None:
    """Return the event loop the calling thread runs, `None` on a thread without one."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def server_loop() -> asyncio.AbstractEventLoop | None:
    """Return the running server loop whose `sync_to_async` call runs this thread.

    It reads the markers `async_to_sync` reads, minus the background loop a hop marks.
    """
    loop = parent_loop()
    if loop is None or loop is background_loop.current() or not loop.is_running():
        return None
    return loop


def launch(
    begin: Callable[[list[asyncio.Task[Any]] | None], object],
) -> asyncio.AbstractEventLoop:
    """Call `begin` on the server loop running this thread, else on the background one.

    On the server loop it gets the list `async_to_sync` cancels its own tasks through.
    """
    loop = server_loop()
    if loop is None:
        return background_loop.call_soon(begin, None)
    loop.call_soon_threadsafe(begin, parent_tasks())
    return loop


def listed(task: asyncio.Task[Any], parents: list[asyncio.Task[Any]] | None) -> None:
    """List `task` in `parents` while it runs, so cancelling the caller cancels it."""
    if parents is not None:
        parents.append(task)
        task.add_done_callback(parents.remove)


__all__ = [
    "BackgroundLoop",
    "background_loop",
    "launch",
    "listed",
    "running_loop",
    "server_loop",
]
