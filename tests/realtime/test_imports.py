import subprocess
import sys

from tests.django_setup import PROJECT_ROOT


def test_core_realtime_import_does_not_load_optional_packages() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "\n"
                "from django.conf import settings\n"
                "\n"
                "from tests.django_setup import build_test_settings\n"
                "\n"
                "settings.configure(**build_test_settings())\n"
                "import next.realtime\n"
                "\n"
                "loaded = sorted(\n"
                "    name\n"
                "    for name in sys.modules\n"
                "    if name.partition('.')[0] in {'channels', 'redis'}\n"
                ")\n"
                "assert not loaded, loaded\n"
            ),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
