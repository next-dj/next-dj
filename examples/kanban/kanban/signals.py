from django.conf import settings

from next.static.assets import StaticAsset
from next.static.signals import collector_finalized


_VITE_ORIGIN = "http://localhost:5173"


def inject_vite_client(sender: object, **_kwargs: object) -> None:
    sender.add(  # type: ignore[attr-defined]
        StaticAsset(
            url="",
            kind="js",
            inline=f'<script type="module" src="{_VITE_ORIGIN}/@vite/client"></script>',
        ),
        prepend=True,
    )


if settings.DEBUG:
    collector_finalized.connect(inject_vite_client)
