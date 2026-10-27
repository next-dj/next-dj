"""The ticket introspection route, served only where a URLconf includes it."""

from django.urls import URLPattern, path

from .views import TicketIntrospectionView


app_name = "next_realtime"
urlpatterns: list[URLPattern] = [
    path("ticket/", TicketIntrospectionView.as_view(), name="ticket")
]
