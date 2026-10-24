import multiprocessing
from dataclasses import dataclass, field


def _process_name() -> str:
    """Return the name of the process building the readiness."""
    return multiprocessing.current_process().name


@dataclass(frozen=True, slots=True)
class Ready:
    """One worker's readiness, with how many of its clients got hello."""

    kind: str
    hello: int = 0
    worker: str = field(default_factory=_process_name)
