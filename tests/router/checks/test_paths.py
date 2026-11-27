import sys
from pathlib import Path
from unittest import mock

import pytest
from django.http import HttpRequest

from next.router.checks import check_root_layout_paths, paths as router_checks
from tests.support.checks import write_file


def path_processor(request: HttpRequest) -> dict[str, object]:
    return {"site_path": request.path, "other": 1}


def plain_processor(_request: HttpRequest) -> dict[str, object]:
    return {"brand": "x"}


_TAGLINE = "Ship it"


def mixed_processor(request: HttpRequest) -> dict[str, object]:
    return {"current_path": request.path, "tagline": _TAGLINE, "who": request.user}


def local_processor(request: HttpRequest) -> dict[str, object]:
    here = request.get_full_path()
    crumbs = here.split("/")
    label: str
    label = _TAGLINE.upper()
    size = (width := len(_TAGLINE))
    return {
        "crumbs": crumbs,
        "label": label,
        "width": width + size,
        "count": len(sys.path),
    }


def opaque_processor(request: HttpRequest) -> dict[str, object]:
    first, *_rest = request.path.split("/")
    return {
        "first": first,
        "nav": _nav(request),
        "host": request.get_host(),
        "match": request.resolver_match,
        "level": len(_TAGLINE),
        **_spread(),
    }


def varargs_processor(*args: HttpRequest) -> dict[str, object]:
    return {"where": args[0].path, "made": _made().path, "fixed": 1}


def bare_processor() -> dict[str, object]:
    return {"fixed": _TAGLINE}


def _nav(request: HttpRequest) -> str:
    return request.path


def _spread() -> dict[str, object]:
    return {}


def _made() -> HttpRequest:
    return HttpRequest()


@pytest.mark.usefixtures("router_on")
class TestRootLayoutPaths:
    """`next.W176` names a root chrome that reads the path outside its zones."""

    def test_path_reads(self, tmp_path: Path) -> None:
        root = write_file(
            tmp_path / "layout.djx",
            "{% comment %}{{ request.path }}{% endcomment %}"
            '{% zone "crumbs" %}{{ request.path }}{% endzone %}'
            "<footer>{{ site_path }} {{ request.get_full_path }}</footer>"
            "{% if brand %}{{ brand }}{% endif %}{% template %}",
        )
        inner = write_file(
            tmp_path / "a" / "layout.djx", "{{ request.path }}{% template %}"
        )
        processors = [path_processor, plain_processor, len]
        with (
            mock.patch.object(router_checks, "layouts", return_value=[root, inner]),
            mock.patch.object(
                router_checks, "context_processors", return_value=processors
            ),
        ):
            (message,) = check_root_layout_paths()
        assert message.id == "next.W176"
        assert "reads site_path, request.get_full_path outside" in message.msg

    def test_a_clean_root(self, tmp_path: Path) -> None:
        root = write_file(tmp_path / "layout.djx", "<main>{% template %}</main>")
        with (
            mock.patch.object(router_checks, "layouts", return_value=[root]),
            mock.patch.object(router_checks, "context_processors", return_value=[]),
        ):
            assert check_root_layout_paths() == []

    def test_an_unreadable_processor_returns_no_key(self) -> None:
        with mock.patch.object(router_checks.inspect, "getsource", side_effect=OSError):
            assert router_checks._path_dependent_keys(path_processor) == frozenset()

    def test_an_unparsable_processor_returns_no_key(self) -> None:
        with mock.patch.object(
            router_checks.inspect, "getsource", return_value="def ("
        ):
            assert router_checks._path_dependent_keys(path_processor) == frozenset()

    @pytest.mark.parametrize(
        ("processor", "keys"),
        [
            (mixed_processor, {"current_path"}),
            (local_processor, {"crumbs"}),
            (opaque_processor, {"first", "nav", "host", "match"}),
            (varargs_processor, {"where", "made"}),
            (bare_processor, set()),
        ],
        ids=["mixed", "locals", "opaque", "varargs", "no_parameter"],
    )
    def test_only_a_key_whose_value_reads_the_path_counts(
        self, processor, keys: set[str]
    ) -> None:
        assert router_checks._path_dependent_keys(processor) == keys

    def test_a_source_without_a_function_counts_every_path_attribute(self) -> None:
        source = "return {'here': request.path, 'there': os.path.sep, 'fixed': 1}\n"
        with mock.patch.object(router_checks.inspect, "getsource", return_value=source):
            assert router_checks._path_dependent_keys(path_processor) == {
                "here",
                "there",
            }

    @pytest.mark.parametrize(
        ("body", "warned"),
        [("{{ tagline }} {{ who }}", False), ("{{ current_path }}", True)],
        ids=["constant", "path"],
    )
    def test_a_constant_of_a_path_processor_is_quiet(
        self, tmp_path: Path, body: str, *, warned: bool
    ) -> None:
        root = write_file(tmp_path / "layout.djx", f"<nav>{body}</nav>{{% template %}}")
        with (
            mock.patch.object(router_checks, "layouts", return_value=[root]),
            mock.patch.object(
                router_checks, "context_processors", return_value=[mixed_processor]
            ),
        ):
            messages = check_root_layout_paths()
        assert [message.id for message in messages] == (["next.W176"] if warned else [])
