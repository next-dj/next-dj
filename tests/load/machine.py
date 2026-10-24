import os
import platform
import subprocess
import sys
from pathlib import Path

import django
import psutil
import redis


CPUINFO = Path("/proc/cpuinfo")


def cpu_model() -> str:
    """Return the CPU brand string, read the way each supported OS exposes it."""
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    elif CPUINFO.exists():
        for line in CPUINFO.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition(":")
            if name.strip() == "model name":
                return value.strip()
    return platform.processor() or platform.machine()


def redis_version(url: str) -> str:
    """Return the server version Redis reports in its INFO section."""
    with redis.Redis.from_url(url) as client:
        return str(client.info("server")["redis_version"])


def describe(url: str) -> dict[str, object]:
    """Return the machine and software stack a result was measured on."""
    return {
        "platform": platform.platform(),
        "cpu_model": cpu_model(),
        "cpu_logical": os.cpu_count(),
        "cpu_physical": psutil.cpu_count(logical=False),
        "memory_total_mb": round(psutil.virtual_memory().total / 2**20),
        "python": platform.python_version(),
        "django": django.get_version(),
        "redis_server": redis_version(url),
    }
