import pytest
from access.demo import seed_demo


@pytest.fixture()
def demo_data(db) -> int:
    """Seed the shipped demo audit log for tests that read it, and count its rows."""
    return seed_demo()
