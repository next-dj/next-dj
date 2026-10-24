from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ready:
    """One worker's readiness, with how many of its clients got hello."""

    kind: str
    hello: int = 0
