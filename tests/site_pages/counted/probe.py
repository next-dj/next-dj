counters: dict[str, int] = {"alpha": 0, "beta": 0, "gamma": 0, "db": 0}


def reset_counters() -> None:
    """Zero every counter so each test starts from a clean slate."""
    for key in counters:
        counters[key] = 0
