import pytest
from kanban.demo import seed_demo


@pytest.fixture()
def demo_data(db) -> None:
    """Seed the shipped demo boards for tests that read them."""
    seed_demo()
