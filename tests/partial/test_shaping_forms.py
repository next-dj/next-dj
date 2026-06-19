from next.partial.headers import CONTENT_TYPE
from next.testing import NextClient, envelope_of
from tests.support import action_uid


class TestInvalidEnvelopeAddressesOnlyTheFailedForm:
    """An invalid submit on a three-form page touches only the failed form.

    The settings page renders three independent forms. Submitting the
    rename form empty addresses one extract-morph of the rename form by
    its uid. The two neighbouring forms are named by no operation, so
    their unsent input is untouched by construction, not by discipline.
    """

    def test_only_the_failed_form_is_a_target(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "rename_board_form", {"title": ""}, origin="/board_forms/", partial=True
        )
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.form_targets() == [action_uid("rename_board_form")]

    def test_neighbouring_form_uids_appear_in_no_target(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "rename_board_form", {"title": ""}, origin="/board_forms/", partial=True
        )
        targets = envelope_of(response).targets()
        column_uid = action_uid("create_column_form")
        archive_uid = action_uid("archive_board_form")
        assert all(target != {"form": column_uid} for target in targets)
        assert all(target != {"form": archive_uid} for target in targets)

    def test_the_only_op_carries_the_extract_flag(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "rename_board_form", {"title": ""}, origin="/board_forms/", partial=True
        )
        assert envelope_of(response).ops[0].get("extract") is True


class TestInvalidEnvelopeHeaderContract:
    """The invalid-form header contract survives the partial path."""

    def test_status_content_type_and_form_headers(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "rename_board_form", {"title": ""}, origin="/board_forms/", partial=True
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        assert response["X-Next-Form"] == "invalid"
        assert response["X-Next-Action"] == action_uid("rename_board_form")


class TestInvalidFormMetaIsMachineReadable:
    """The form meta carries machine-readable errors from the field specs."""

    def test_meta_reports_invalid_with_field_errors(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "rename_board_form", {"title": ""}, origin="/board_forms/", partial=True
        )
        meta = envelope_of(response).form_meta()
        assert meta == {
            "uid": action_uid("rename_board_form"),
            "valid": False,
            "errors": {"title": ["This field is required."]},
        }


class TestInvalidWithZoneMorphsTheZone:
    """A form inside a zone re-renders only that zone with the bound form.

    The page declares the `rename-board` zone, and the partial intent
    names it, so the failed submit morphs the zone instead of extract-
    morphing the whole document. The bound form rides the zone context.
    """

    def test_zone_morph_replaces_extract(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "zoned_rename_form",
            {"title": ""},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
        )
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["rename-board"]
        assert envelope.form_targets() == []

    def test_bound_form_errors_reach_the_zone_body(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "zoned_rename_form",
            {"title": ""},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
        )
        html = envelope_of(response).html_for_zone("rename-board")
        assert "required" in html.lower()

    def test_zone_morph_still_carries_form_meta(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "zoned_rename_form",
            {"title": ""},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
        )
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert meta["valid"] is False


class TestResultRedirectBecomesVisit:
    """A handler redirect packs into a visit, validating the host."""

    def test_internal_redirect_travels_as_internal_visit(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "internal_redirect_form", {"name": "ok"}, origin="/", partial=True
        )
        ops = envelope_of(response).ops
        assert ops == [{"op": "visit", "href": "/done/"}]

    def test_external_redirect_carries_full_navigation_marker(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "external_redirect_form", {"name": "ok"}, origin="/", partial=True
        )
        ops = envelope_of(response).ops
        assert ops == [
            {
                "op": "visit",
                "href": "https://oauth.example.com/authorize",
                "external": True,
            }
        ]


class TestResultAuthoredPatchPassesThrough:
    """A handler that returns a PatchResponse passes through unchanged."""

    def test_authored_envelope_is_served_verbatim(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "authored_patch_form", {"name": "ok"}, origin="/", partial=True
        )
        assert envelope_of(response).op_verbs() == ["toast"]
        assert envelope_of(response).toasts()[0]["text"] == "authored"


class TestResultRichResponseFallsThrough:
    """A non-redirect rich response falls through to the full path."""

    def test_plain_html_response_is_not_an_envelope(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "rich_response_form", {"name": "ok"}, origin="/", partial=True
        )
        assert response["Content-Type"] != CONTENT_TYPE
        assert response.content == b"<p>plain</p>"


class TestSuccessFunnelMorphsAndDrainsMessages:
    """A None result morphs the form zone and drains messages to toasts.

    The zoned rename form flashes a success message and an info note on a
    valid submit. The success funnel morphs the zone in place and drains
    both pending messages into toast patches.
    """

    def test_zone_morph_and_message_toasts(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "zoned_rename_form",
            {"title": "Renamed"},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
        )
        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["rename-board"]
        toast_texts = {toast["text"] for toast in envelope.toasts()}
        assert toast_texts == {"Board renamed", "secondary note"}

    def test_success_omits_the_invalid_headers(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "zoned_rename_form",
            {"title": "Renamed"},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
        )
        assert "X-Next-Form" not in response

    def test_a_repeat_request_does_not_replay_the_drained_messages(
        self, next_client: NextClient
    ) -> None:
        first = next_client.post_action(
            "zoned_rename_form",
            {"title": "Renamed"},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
        )
        assert len(envelope_of(first).toasts()) == 2
        second = next_client.post_action(
            "zoned_rename_form",
            {"title": "Again"},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
        )
        # the drained messages of the first request are read, so the second
        # request flashes its own two and replays none of the first
        assert len(envelope_of(second).toasts()) == 2


class TestSuccessFunnelExtractMorphWithoutZone:
    """Without a named zone the success funnel extract-morphs the form."""

    def test_extract_morph_of_the_form_by_uid(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "regression_form", {"name": "ok"}, origin="/", partial=True
        )
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.form_targets() == [action_uid("regression_form")]
        assert envelope.ops[0].get("extract") is True
