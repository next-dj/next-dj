from tests.django_setup import setup as _configure_django_for_tests


_configure_django_for_tests()


# Shared fixtures
pytest_plugins = ["tests.fixtures"]
