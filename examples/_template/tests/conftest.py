import pytest
from django.contrib.auth import get_user_model
from myapp.services import create_item


@pytest.fixture()
def alice(db):
    return get_user_model().objects.create_user("alice", password="secret")


@pytest.fixture()
def bob(db):
    return get_user_model().objects.create_user("bob", password="secret")


@pytest.fixture()
def superuser(db):
    return get_user_model().objects.create_superuser("admin", password="secret")


@pytest.fixture()
def mine(alice):
    return create_item(alice, title="Mine")


@pytest.fixture()
def theirs(bob):
    return create_item(bob, title="Theirs")
