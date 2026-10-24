import os

from django.apps import apps
from django.core.asgi import get_asgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
if apps.is_installed("channels"):
    from next.contrib.channels import application as realtime_application

    application = realtime_application(application)
