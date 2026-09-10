import hashlib
import os
import pathlib
import re
import tempfile
import uuid
from collections.abc import Generator, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import NamedTuple

import pytest
from playwright.sync_api import ConsoleMessage, Error, Page, Request, Response, Route


CDN_URL_PATTERN = re.compile(
    r"^https://(?:cdn\.tailwindcss\.com|cdn\.jsdelivr\.net|unpkg\.com)(?:/|$)"
)

# Play CDN compiles Tailwind in the browser and every navigation refetches ~400 KB,
# so the bytes are cached on disk once per machine rather than per xdist worker.
CDN_CACHE_DIR = pathlib.Path(tempfile.gettempdir()) / "next-dj-e2e-cdn"

IGNORED_CONSOLE = (
    re.compile(r"cdn\.tailwindcss\.com should not be used in production"),
    re.compile(r"Third-party cookie will be blocked"),
    re.compile(r"You are using the in-browser Babel transformer"),
)

HTTP_ERROR_STATUS = 400

# A superseded GET is aborted on purpose by the latest-wins queue in wire.ts.
BENIGN_FAILURES = ("net::ERR_ABORTED",)

IGNORED_URLS = (re.compile(r"/favicon\.ico(?:\?|$)"), re.compile(r"\.map(?:\?|$)"))

# Neither event channel of the runtime carries every event, so the bridge takes both.
BUS_EVENTS = (
    "ready",
    "context-updated",
    "partial:before-request",
    "partial:before-apply",
    "partial:applied",
    "partial:error",
    "partial:layer-opened",
    "partial:layer-accepted",
    "partial:layer-dismissed",
    "next:toast",
)

DOM_EVENTS = (
    "next:mounted",
    "next:removed",
    "next:morph-element",
    "next:morph-attribute",
)

EVENT_BRIDGE = """
(() => {
  window.__nextEvents = [];
  const record = (name, channel) => {
    window.__nextEvents.push({ name, channel, at: Date.now() });
  };
  for (const name of %(dom)s) {
    document.addEventListener(name, () => record(name, "dom"));
  }
  const subscribe = () => {
    if (!window.Next || typeof window.Next.on !== "function") {
      setTimeout(subscribe, 0);
      return;
    }
    for (const name of %(bus)s) {
      window.Next.on(name, () => record(name, "bus"));
    }
  };
  subscribe();
})();
"""


def pytest_configure() -> None:
    """Let the suite reach the ORM from the thread playwright parks its loop in.

    The sync API keeps a running asyncio loop in a greenlet of the main thread, so
    Django's async guard fires on the ORM calls `transactional_db` makes from there
    and every browser test errors out before it starts. Living in the plugin rather
    than in a conftest keeps the guard armed for every suite that never loads it.
    """
    os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")


@dataclass
class PageProbe:
    """Collects browser-side noise so a test fails on it instead of ignoring it."""

    console: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    bad_responses: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    responses: list[Response] = field(default_factory=list)
    page: Page | None = None

    def on_console(self, message: ConsoleMessage) -> None:
        """Record an error or warning the page logged."""
        if message.type not in {"error", "warning"}:
            return
        if any(pattern.search(message.text) for pattern in IGNORED_CONSOLE):
            return
        location = message.location
        where = f"{location.get('url')}:{location.get('lineNumber')}"
        self.console.append(f"[{message.type}] {message.text} ({where})")

    def on_page_error(self, error: Error) -> None:
        """Record an uncaught exception, the loudest signal a page can give."""
        self.page_errors.append(f"{error.name}: {error.message}")

    def on_response(self, response: Response) -> None:
        """Keep every response and flag the failing ones."""
        self.responses.append(response)
        if response.status < HTTP_ERROR_STATUS:
            return
        if any(pattern.search(response.url) for pattern in IGNORED_URLS):
            return
        self.bad_responses.append(f"{response.status} {response.url}")

    def on_request_failed(self, request: Request) -> None:
        """Record a request the browser could not finish."""
        failure = request.failure or ""
        if any(benign in failure for benign in BENIGN_FAILURES):
            return
        if any(pattern.search(request.url) for pattern in IGNORED_URLS):
            return
        self.failed_requests.append(f"{failure} {request.url}")

    def attach(self, page: Page) -> None:
        """Subscribe to the four channels that carry browser problems."""
        self.page = page
        page.on("console", self.on_console)
        page.on("pageerror", self.on_page_error)
        page.on("response", self.on_response)
        page.on("requestfailed", self.on_request_failed)

    def detach(self, page: Page) -> None:
        """Unsubscribe so a teardown navigation cannot append late noise."""
        page.remove_listener("console", self.on_console)
        page.remove_listener("pageerror", self.on_page_error)
        page.remove_listener("response", self.on_response)
        page.remove_listener("requestfailed", self.on_request_failed)

    def flush(self) -> None:
        """Round-trip to the browser so late events land before the noise is read.

        Playwright delivers protocol events while the sync API waits on a call, so a
        message the last statement of a test logged needs one more call to arrive.
        """
        if self.page is None or self.page.is_closed():
            return
        with suppress(Error):
            self.page.evaluate("() => undefined")

    def problems(self) -> list[str]:
        """Return every recorded problem as a readable line."""
        return (
            [f"pageerror: {entry}" for entry in self.page_errors]
            + [f"console: {entry}" for entry in self.console]
            + [f"http: {entry}" for entry in self.bad_responses]
            + [f"netfail: {entry}" for entry in self.failed_requests]
        )

    def partial_requests(
        self, zone: str | None = None, *, since: int = 0
    ) -> list[Response]:
        """Return responses the runtime stamped as partial, optionally one zone.

        A batched trigger sends every target in one comma-separated header, so a
        zone matches when it appears in that list rather than equalling it. The
        `since` index skips the responses a caller already accounted for.
        """
        matched = []
        for response in self.responses[since:]:
            headers = response.request.headers
            if headers.get("x-next-request") != "1":
                continue
            if zone is not None:
                targets = headers.get("x-next-zone", "").split(",")
                if zone not in [target.strip() for target in targets]:
                    continue
            matched.append(response)
        return matched


PROBE_KEY = pytest.StashKey[PageProbe]()


def _cache_path(url: str) -> pathlib.Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    suffix = ".css" if url.endswith(".css") else ".js"
    return CDN_CACHE_DIR / f"{digest}{suffix}"


def _content_type(path: pathlib.Path) -> str:
    return "text/css" if path.suffix == ".css" else "application/javascript"


def _serve_from_cache(route: Route) -> None:
    path = _cache_path(route.request.url)
    if not path.exists():
        fetched = route.fetch()
        if not fetched.ok:
            # An error page cached under a script name would be replayed as a valid
            # bundle by every later run on this machine, so nothing is stored and the
            # test sees the real status instead.
            route.fulfill(response=fetched)
            return
        CDN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Another xdist worker may be writing the same asset, so publish atomically
        # under a name no concurrent process on this machine can pick as well.
        staging = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex}")
        staging.write_bytes(fetched.body())
        staging.replace(path)
    route.fulfill(status=200, content_type=_content_type(path), body=path.read_bytes())


def _test_failed(request: pytest.FixtureRequest) -> bool:
    """Report whether the call phase failed, the way pytest-playwright reads it.

    The report attribute is missing when the test never reached its call phase,
    which counts as a failure so the artefacts survive.
    """
    report = getattr(request.node, "rep_call", None)
    return report is None or bool(report.failed)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Fail the call phase, not the teardown, on the noise the probe collected.

    pytest-playwright keeps the retain-on-failure trace, video and screenshot only
    when `rep_call` failed, so a probe failing in teardown would delete its own
    evidence.
    """
    outcome = yield
    probe = item.stash.get(PROBE_KEY, None)
    if probe is None:
        return outcome
    probe.flush()
    problems = probe.problems()
    if problems:
        pytest.fail("the browser reported problems:\n  " + "\n  ".join(problems))
    return outcome


@pytest.fixture()
def next_probe(request: pytest.FixtureRequest) -> Iterator[PageProbe]:
    """Stub the CDN, bridge the runtime events, and collect browser noise."""
    page: Page = request.getfixturevalue("page")
    page.context.route(CDN_URL_PATTERN, _serve_from_cache)
    bridge = EVENT_BRIDGE % {"dom": list(DOM_EVENTS), "bus": list(BUS_EVENTS)}
    page.add_init_script(bridge)

    collected = PageProbe()
    collected.attach(page)
    request.node.stash[PROBE_KEY] = collected
    yield collected
    collected.detach(page)

    if not _test_failed(request):
        # Closing here releases any SSE stream still parked in a server thread. A
        # failed test keeps its page open instead, because pytest-playwright shoots
        # the failure screenshot off `context.pages` when the context tears down.
        page.close()


@pytest.fixture(autouse=True)
def _next_browser_probe(request: pytest.FixtureRequest) -> None:
    """Attach the probe to every test that drives a browser, and only to those."""
    if "page" not in request.fixturenames:
        return
    request.getfixturevalue("next_probe")


def wait_for_runtime(page: Page, timeout: int = 10_000) -> None:
    """Block until the client runtime has booted and replayed its ready event."""
    page.wait_for_function(
        "() => window.Next !== undefined && window.Next.context !== undefined",
        timeout=timeout,
    )


def applied_count(page: Page) -> int:
    """Return how many patch applications the bridge has recorded so far."""
    return page.evaluate(
        "() => (window.__nextEvents ?? [])"
        ".filter((entry) => entry.name === 'partial:applied').length"
    )


def wait_for_apply(page: Page, since: int, timeout: int = 10_000) -> None:
    """Block until one more patch lands than the caller already saw.

    A response is not enough because the applicator holds its operations behind
    the CSS gate until the stylesheets of the patch have loaded.
    """
    page.wait_for_function(
        "(seen) => (window.__nextEvents ?? [])"
        ".filter((entry) => entry.name === 'partial:applied').length > seen",
        arg=since,
        timeout=timeout,
    )


def _requested_count(page: Page) -> int:
    """Return how many partial requests the bridge has recorded leaving so far."""
    return page.evaluate(
        "() => (window.__nextEvents ?? [])"
        ".filter((entry) => entry.name === 'partial:before-request').length"
    )


class RequestBaseline(NamedTuple):
    """What an interaction is measured against, as counted before it ran."""

    sent: int = 0
    answered: int = 0


# The counts a page that has just loaded starts from.
PAGE_START = RequestBaseline()


def request_baseline(page: Page, probe: PageProbe) -> RequestBaseline:
    """Return the counts a later `expect_no_partial_request` opens its window at."""
    return RequestBaseline(_requested_count(page), len(probe.responses))


def expect_no_partial_request(
    page: Page, probe: PageProbe, since: RequestBaseline = PAGE_START
) -> None:
    """Fail unless the interaction since the baseline left the server untouched.

    The runtime dispatches `partial:before-request` synchronously before it awaits the
    fetch, so the event is already recorded once the playwright call that triggered it
    returns. The response listener alone would pass while the request is still in
    flight, which is the very regression these assertions guard against, and it stays
    only as a second reading that catches a bridge which never installed.
    """
    sent = _requested_count(page) - since.sent
    if sent > 0:
        pytest.fail(f"the runtime sent {sent} partial request(s), expected none")
    answered = probe.partial_requests(since=since.answered)
    if answered:
        urls = "\n  ".join(response.url for response in answered)
        pytest.fail(f"the server answered partial requests:\n  {urls}")
