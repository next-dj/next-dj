import pytest
from catalog.demo import seed_demo


@pytest.fixture()
def demo_data(db) -> None:
    """Seed the shipped demo catalog for tests that read it."""
    seed_demo()
