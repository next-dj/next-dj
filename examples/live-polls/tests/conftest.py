import pytest
from polls.demo import seed_demo
from polls.models import Poll


@pytest.fixture()
def demo_data(db) -> None:
    """Seed the shipped demo polls for tests that read them."""
    seed_demo()


@pytest.fixture()
def poll(demo_data) -> Poll:
    """Return the first demo poll with its two choices."""
    return Poll.objects.get(question="Tabs or spaces?")


@pytest.fixture()
def second_poll(demo_data) -> Poll:
    """Return the second demo poll, used to exercise cross-poll validation."""
    return Poll.objects.get(question="Vim or Emacs?")
