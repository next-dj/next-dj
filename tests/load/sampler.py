import threading
from dataclasses import dataclass, field
from typing import Self

import psutil


@dataclass(slots=True)
class ResourceSampler:
    """Sample one process's CPU and RSS on a fixed interval from a daemon thread."""

    pid: int
    interval: float = 0.5
    cpu: list[float] = field(default_factory=list)
    rss: list[int] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        """Begin sampling and return the sampler."""
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop sampling however the block ended."""
        self.stop()

    def start(self) -> None:
        """Prime the CPU counter and begin sampling."""
        process = psutil.Process(self.pid)
        process.cpu_percent(None)
        self._thread = threading.Thread(target=self._loop, args=(process,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and wait for the thread to exit."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self, process: psutil.Process) -> None:
        """Take one sample per interval until stopped or the process is gone."""
        while not self._stop.wait(self.interval):
            try:
                with process.oneshot():
                    self.cpu.append(process.cpu_percent(None))
                    self.rss.append(process.memory_info().rss)
            except psutil.Error:
                return

    def summary(self) -> dict[str, float | int | None]:
        """Return the average and peak CPU percent and the peak RSS in megabytes."""
        if not self.cpu:
            return {"samples": 0, "cpu_avg": None, "cpu_max": None, "rss_max_mb": None}
        return {
            "samples": len(self.cpu),
            "cpu_avg": round(sum(self.cpu) / len(self.cpu), 1),
            "cpu_max": round(max(self.cpu), 1),
            "rss_max_mb": round(max(self.rss) / 2**20, 1),
        }
