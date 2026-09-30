import pytest
from django.template import Context, Template

from next.consent import NECESSARY, UNDECIDED, Consent


class TestConsent:
    """A consent answers per category, necessary always granted."""

    def test_an_undecided_visitor_grants_only_necessary(self) -> None:
        assert Consent(frozenset({NECESSARY}), decided=False) == UNDECIDED
        assert UNDECIDED.allows(NECESSARY)
        assert not UNDECIDED.allows("marketing")

    def test_necessary_holds_even_when_not_listed(self) -> None:
        consent = Consent(frozenset({"analytics"}), decided=True)
        assert consent.allows(NECESSARY)
        assert consent.allows("analytics")
        assert not consent.allows("marketing")

    def test_an_item_lookup_answers_like_allows(self) -> None:
        consent = Consent(frozenset({"analytics"}), decided=True)
        assert consent["analytics"] is True
        assert consent["marketing"] is False

    @pytest.mark.parametrize("name", ["allows", "decided", "granted"])
    def test_a_field_name_is_no_category(self, name: str) -> None:
        with pytest.raises(KeyError):
            Consent()[name]

    def test_a_template_reads_categories_and_fields(self) -> None:
        template = Template(
            "{% if consent.marketing %}m{% endif %}"
            "{% if consent.analytics %}a{% endif %}"
            "{% if consent.decided %}d{% endif %}"
        )
        consent = Consent(frozenset({"analytics"}), decided=True)
        assert template.render(Context({"consent": consent})) == "ad"
