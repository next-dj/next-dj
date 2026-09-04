from django.http import HttpRequest
from django.test import RequestFactory

from next.deps import DependencyResolver, resolver
from next.forms import DForm
from next.testing import override_provider
from next.urls import DQuery
from tests.support import AForm, DeferringProvider, OtherForm, build_mock_http_request


def _theme(theme: HttpRequest | None = None) -> None:
    return None


def _flag(flag: str = "") -> None:
    return None


def _forms(
    form=None, exact: DForm[AForm] = None, wrong: DForm[OtherForm] = None
) -> None:
    return None


def _query(page: DQuery[int] = 1) -> None:
    return None


class TestPriorityOutcomes:
    """The plan keeps the priority verdict wherever the signature leaves it open."""

    def test_context_name_outranks_the_request_annotation(self) -> None:
        request = build_mock_http_request()
        in_context = {"request": request, "_context_data": {"theme": "ctx"}}
        assert resolver.resolve_dependencies(_theme, **in_context) == {"theme": "ctx"}
        assert resolver.resolve_dependencies(_theme, request=request) == {
            "theme": request
        }
        assert resolver.resolve_dependencies(_theme) == {"theme": None}

    def test_protocol_provider_deferring_its_verdict_is_consulted_at_resolve(
        self,
    ) -> None:
        instance = DependencyResolver()
        assert instance.resolve_dependencies(_flag) == {"flag": ""}
        instance.add_provider(DeferringProvider("flag"))
        assert instance.resolve_dependencies(_flag) == {"flag": "STUB"}

    def test_override_provider_wins_inside_the_block_only(self) -> None:
        assert resolver.resolve_dependencies(_flag) == {"flag": ""}
        with override_provider(DeferringProvider("flag")):
            assert resolver.resolve_dependencies(_flag) == {"flag": "STUB"}
        assert resolver.resolve_dependencies(_flag) == {"flag": ""}

    def test_dform_matches_only_the_bound_form_class(self) -> None:
        form = AForm()
        assert resolver.resolve_dependencies(_forms, form=form) == {
            "form": form,
            "exact": form,
            "wrong": None,
        }
        assert resolver.resolve_dependencies(_forms) == {
            "form": None,
            "exact": None,
            "wrong": None,
        }

    def test_dquery_falls_back_without_a_request(self) -> None:
        assert resolver.resolve_dependencies(_query) == {"page": 1}
        request = RequestFactory().get("/?page=3")
        assert resolver.resolve_dependencies(_query, request=request) == {"page": 3}
