from typing import ClassVar
from unittest.mock import MagicMock

from django import forms as django_forms
from django.http import HttpRequest, QueryDict

from next.forms import Form
from next.forms._request_utils import _url_kwargs_from_resolver_or_post
from next.forms.dispatch import _form_action_context_callable


class TestUrlKwargsFromResolverOrPost:
    """`_url_kwargs_from_resolver_or_post` drops the dispatch `uid` kwarg."""

    def test_uid_only_resolver_kwargs_fall_back_to_post(
        self, mock_http_request
    ) -> None:
        """The dispatch route's lone uid kwarg yields the posted page params."""
        post = QueryDict(mutable=True)
        post["_url_param_pk"] = "5"
        match = MagicMock()
        match.kwargs = {"uid": "abcdef1234567890"}
        req = mock_http_request(method="POST", POST=post, resolver_match=match)
        assert _url_kwargs_from_resolver_or_post(req) == {"pk": 5}

    def test_uid_dropped_alongside_real_kwargs(self, mock_http_request) -> None:
        """A uid kwarg is stripped while genuine URL kwargs pass through."""
        match = MagicMock()
        match.kwargs = {"uid": "abcdef1234567890", "slug": "tea"}
        req = mock_http_request(method="GET", resolver_match=match)
        assert _url_kwargs_from_resolver_or_post(req) == {"slug": "tea"}

    def test_resolver_kwargs_without_uid_pass_through(self, mock_http_request) -> None:
        """Resolver kwargs without a uid stay untouched."""
        match = MagicMock()
        match.kwargs = {"slug": "tea"}
        req = mock_http_request(method="GET", resolver_match=match)
        assert _url_kwargs_from_resolver_or_post(req) == {"slug": "tea"}

    def test_uid_only_get_returns_empty(self, mock_http_request) -> None:
        """A GET with only the uid kwarg yields no URL kwargs."""
        match = MagicMock()
        match.kwargs = {"uid": "abcdef1234567890"}
        req = mock_http_request(method="GET", resolver_match=match)
        assert _url_kwargs_from_resolver_or_post(req) == {}


class TestSiblingFormReRenderKwargs:
    """Sibling forms built on a dispatch POST never see the dispatch uid."""

    def test_sibling_get_initial_receives_posted_page_params(
        self, mock_http_request
    ) -> None:
        """`get_initial` gets the posted `_url_param_*` kwargs, not the uid."""

        class SiblingInitialForm(Form):
            name = django_forms.CharField(max_length=10, required=False)

            seen: ClassVar[list] = []

            @classmethod
            def get_initial(cls, request: HttpRequest, **kwargs: object) -> dict:
                cls.seen.append(dict(kwargs))
                return {}

        post = QueryDict(mutable=True)
        post["_url_param_pk"] = "5"
        match = MagicMock()
        match.kwargs = {"uid": "abcdef1234567890"}
        req = mock_http_request(method="POST", POST=post, resolver_match=match)
        _form_action_context_callable(SiblingInitialForm)(req)
        assert SiblingInitialForm.seen == [{"pk": 5}]
