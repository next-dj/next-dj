import asyncio
import importlib
import json
import sys
import threading

from asgiref.sync import sync_to_async
from django.conf import settings

from tests.django_setup import build_test_settings


async def _hop() -> int:
    await asyncio.sleep(0)
    return await sync_to_async(threading.get_ident)()


def main() -> None:
    """Import the modules named on stdin, then run async code from sync code.

    The framework loads by name once settings exist, so nothing imports it before.
    """
    settings.configure(**build_test_settings())
    for name in json.load(sys.stdin):
        importlib.import_module(name)
    drive = importlib.import_module("next.bridge").drive
    bridge = importlib.import_module("next.deps.runner").bridge
    bridged_scope = importlib.import_module("next.deps.scope").bridged_scope
    here = threading.get_ident()
    answered = {
        "drive": drive(_hop) == here,
        "bridge": bridge("probe", _hop) == here,
        "scope": bridged_scope({}, _hop) == here,
    }
    loaded = sorted(name for name in sys.modules if name.startswith("next."))
    sys.stdout.write(json.dumps({"answered": answered, "loaded": loaded}))


if __name__ == "__main__":
    main()
