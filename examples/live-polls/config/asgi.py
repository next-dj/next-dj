import os

from django.core.asgi import get_asgi_application

from next import realtime


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = realtime.application(get_asgi_application())
