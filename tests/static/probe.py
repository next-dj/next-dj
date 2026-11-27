import importlib
import json
import sys

from django.conf import settings

from tests.django_setup import build_test_settings


PAGE = "<head><!-- next:styles --></head><body><p>x</p><!-- next:scripts --></body>"


def main() -> None:
    """Inject one page with the kernel alone, no app ready and no feature imported.

    The framework loads by name once settings exist, so nothing imports it before.
    """
    settings.configure(**build_test_settings())
    static = importlib.import_module("next.static")
    ports = importlib.import_module("next.ports")
    static.register_defaults()
    manager = static.StaticManager()
    html = manager.inject(PAGE, manager.create_collector())
    unbound = sorted(
        name
        for name, slot in vars(ports).items()
        if isinstance(slot, ports.PortSlot) and slot.peek() is not None
    )
    loaded = sorted(name for name in sys.modules if name.startswith("next."))
    providers = [item.name for item in ports.page_scripts]
    sys.stdout.write(
        json.dumps(
            {"html": html, "loaded": loaded, "bound": unbound, "providers": providers}
        )
    )


if __name__ == "__main__":
    main()
