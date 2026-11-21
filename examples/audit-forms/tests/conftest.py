import pytest
from access.demo import seed_demo


@pytest.fixture()
def demo_data(db) -> None:
    """Seed the shipped demo audit log for tests that read it."""
    seed_demo()
