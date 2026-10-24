from typing import TYPE_CHECKING, cast

from django.core.asgi import get_asgi_application

from next.contrib.channels import application as realtime_application


if TYPE_CHECKING:
    from asgiref.typing import ASGIApplication


application = realtime_application(cast("ASGIApplication", get_asgi_application()))
