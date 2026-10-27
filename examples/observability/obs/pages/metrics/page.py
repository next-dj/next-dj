from django.http import HttpRequest, HttpResponse
from obs.exporter import check_scrape
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest


def render(request: HttpRequest) -> HttpResponse:
    """Answer a Prometheus scrape with every series of the default registry."""
    check_scrape(request)
    return HttpResponse(generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)
