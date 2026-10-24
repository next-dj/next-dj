import resource
from contextlib import suppress


OPEN_FILES_CAP = 10240


def raise_open_files() -> None:
    """Lift the soft open-file limit toward the hard one, capped at the cap.

    macOS starts at 256 descriptors and refuses a limit above its OPEN_MAX of 10240.
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = (
        OPEN_FILES_CAP if hard == resource.RLIM_INFINITY else min(hard, OPEN_FILES_CAP)
    )
    if soft != resource.RLIM_INFINITY and soft < target:
        with suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
