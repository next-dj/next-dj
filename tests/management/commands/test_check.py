import subprocess
import sys


class TestCheckCommand:
    """The `check` command loads the boundary engine only for the boundaries tag."""

    def test_the_command_loads_no_engine(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys, next.management.commands.check\n"
                    "loaded = sorted(m for m in sys.modules "
                    "if m.startswith('next.boundaries') or 'grimp' in m)\n"
                    "print(','.join(loaded))\n"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == (
            "next.boundaries,next.boundaries.errors,next.boundaries.signals"
        )
