from collections.abc import Iterator
from contextlib import ExitStack
from unittest import mock

import pytest

from next.forms.backends import ResponseShaper
from next.forms.manager import form_action_manager
from next.partial import FormShaper
from next.partial.headers import CONTENT_TYPE, VALIDATE
from next.testing import NextClient
from tests.support import parameter_shape, port_methods
from tests.support.forms import DecliningShaper


@pytest.fixture()
def declining_shaper() -> Iterator[None]:
    """Answer every form backend through a shaper that declines everything."""
    with ExitStack() as stack:
        for backend in form_action_manager.backends:
            stack.enter_context(mock.patch.object(backend, "shaper", DecliningShaper()))
        yield


class TestFormShaperContract:
    """The default shaper keeps the exact call shape the strategy declares."""

    def test_the_strategy_declares_shape_and_validate(self) -> None:
        assert port_methods(ResponseShaper) == ["shape", "validate"]

    @pytest.mark.parametrize("name", port_methods(ResponseShaper))
    def test_form_shaper_parameters_match_the_strategy(self, name) -> None:
        assert parameter_shape(FormShaper, name) == parameter_shape(
            ResponseShaper, name
        )

    def test_form_shaper_satisfies_the_runtime_check(self) -> None:
        assert isinstance(FormShaper(), ResponseShaper)


@pytest.mark.django_db()
class TestDecliningShaper:
    """A shaper answering `None` leaves the request to the default pipeline."""

    @pytest.mark.usefixtures("declining_shaper")
    def test_a_declined_validate_pass_runs_the_full_submission(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "validate_form",
            {"email": "bad"},
            origin="/",
            partial=True,
            headers={VALIDATE: "email"},
        )
        assert response.status_code == 200
        assert response["Content-Type"] != CONTENT_TYPE
        assert response["X-Next-Form"] == "invalid"
