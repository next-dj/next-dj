import pytest
from notes.demo import seed_demo


@pytest.fixture()
def demo_data(db) -> None:
    """Seed the shipped demo tenants and notes for tests that read them."""
    seed_demo()
