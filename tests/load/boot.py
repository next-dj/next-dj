import os

from tests.load.limits import raise_open_files


SETTINGS_MODULE = "tests.load.settings"


def boot() -> None:
    """Prepare a spawned process, lifting its descriptor limit and naming the settings.

    The settings module is set before anything reads the Django configuration.
    """
    raise_open_files()
    os.environ["DJANGO_SETTINGS_MODULE"] = SETTINGS_MODULE
