from shortener.cache import CLICK_PREFIX
from shortener.models import Link
from shortener.providers import DLink

from next.pages import context


@context
def link_context(link: DLink[Link]) -> dict[str, object]:
    return {
        "link": link,
        "cache_key": f"{CLICK_PREFIX}{link.slug}",
    }
