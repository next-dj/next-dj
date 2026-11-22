import json
import re
from pathlib import Path

from django.http import HttpResponseBase
from django.test import RequestFactory

from next.pages import page
from tests.support.pages import unified_view


ROOT_LAYOUT = (
    '<!doctype html><html lang="en"><head>{% metadata %}{% collect_styles %}</head>'
    '<body{% if theme %} class="{{ theme }}"{% endif %}><header>'
    '{% zone "flash" %}<p>{{ flash|default:"none" }}</p>{% endzone %}'
    "</header>{% template %}{% collect_scripts %}</body></html>"
)
SECTION_LAYOUT = "<section><h2>Section {{ title }}</h2>{% template %}</section>"
NAVIGATION = {
    "X-Next-Request": "1",
    "X-Next-Navigate": "1",
    "X-Next-Origin": "/sec/a/",
    "Sec-Fetch-Site": "same-origin",
}
_LAYOUTS = re.compile(r'"\$layouts":(\[.*?\]\])')
_THEMED = "@context('theme')\ndef theme():\n    return 'dark'\n"
_FACTORY = RequestFactory()


def write_layout_page(directory: Path, body: str, source: str = "") -> Path:
    """Write a page under `directory` whose title is `same` and return its `page.py`."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        "from next.pages import context, page\n"
        "@context('title')\n"
        "def title():\n"
        "    return 'same'\n" + source
    )
    (directory / "template.djx").write_text(body)
    return page_file


def layout_tree(root: Path) -> dict[str, Path]:
    """Write a root layout, a `sec` layout and pages under both, keyed by short name."""
    root.mkdir()
    (root / "layout.djx").write_text(ROOT_LAYOUT)
    section = root / "sec"
    section.mkdir()
    (section / "layout.djx").write_text(SECTION_LAYOUT)
    built = {
        "a": write_layout_page(section / "a", "<h1>A</h1>"),
        "b": write_layout_page(section / "b", "<h1>B</h1>"),
        "other": write_layout_page(root / "other", "<h1>other</h1>"),
        "slow": write_layout_page(
            section / "slow", "<h1>{{ late }}</h1>", "late = 1\n"
        ),
        "opted": write_layout_page(
            section / "opted",
            "<h1>opted</h1>",
            "page.router(prefetch=False, snapshot=False)\n",
        ),
        "hard": write_layout_page(
            section / "hard", "<h1>hard</h1>", "page.router(navigate=False)\n"
        ),
        "themed": write_layout_page(section / "themed", "<h1>themed</h1>", _THEMED),
    }
    (section / "slow" / "loading.djx").write_text("<p>loading</p>")
    return built


def get_page(page_file: Path, path: str = "/", **headers: str) -> HttpResponseBase:
    """Answer a GET of `path` through the unified view of `page_file`."""
    return unified_view(page, page_file)(_FACTORY.get(path, headers=headers))


def navigate(
    page_file: Path, path: str = "/sec/b/", layouts: str | None = None, **headers: str
) -> HttpResponseBase:
    """Answer the navigation the client router sends from `/sec/a/`, `headers` on top."""
    sent = {**NAVIGATION, **headers}
    if layouts is not None:
        sent["X-Next-Layouts"] = layouts
    return get_page(page_file, path, **sent)


def layouts_of(response: HttpResponseBase) -> list[list[str]]:
    """Return the `$layouts` chain of a document, outer first."""
    found = _LAYOUTS.search(response.content.decode())
    assert found is not None
    return json.loads(found.group(1))


def declare(layouts: list[list[str]]) -> str:
    """Spell a `$layouts` chain as the `X-Next-Layouts` header the client sends."""
    return ",".join(f"{ident}.{digest}" for ident, digest, _seal in layouts)


def declared_by(page_file: Path, path: str = "/sec/a/") -> str:
    """Return the layouts header a client holding the document of `path` sends."""
    return declare(layouts_of(get_page(page_file, path)))


def envelope_json(response: HttpResponseBase) -> dict[str, object]:
    """Return the JSON envelope a navigation answers with."""
    return json.loads(response.content)


def ops(response: HttpResponseBase) -> list[dict[str, object]]:
    """Return the operations of the envelope, in order."""
    found = envelope_json(response)["ops"]
    assert isinstance(found, list)
    return found


def op_targets(response: HttpResponseBase) -> list[tuple[str, object]]:
    """Return each operation of the envelope as its verb and target."""
    return [(op["op"], op.get("target")) for op in ops(response)]
