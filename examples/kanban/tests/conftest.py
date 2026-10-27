import pytest
from django.contrib.auth.models import User
from kanban.demo import seed_demo, seed_members


@pytest.fixture(autouse=True)
def fast_passwords(settings) -> None:
    """Hash demo passwords with MD5 so signing members in costs no PBKDF2 rounds."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture()
def demo_data(db) -> None:
    """Seed the shipped demo boards for tests that read them."""
    seed_demo()


@pytest.fixture()
def ada(db) -> User:
    """Return the demo member Ada Lovelace."""
    seed_members()
    return User.objects.get(username="ada")


@pytest.fixture()
def grace(db) -> User:
    """Return the demo member Grace Hopper."""
    seed_members()
    return User.objects.get(username="grace")
