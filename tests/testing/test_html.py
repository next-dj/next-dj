import pytest

from next.testing.html import (
    assert_has_class,
    assert_missing_class,
    find_anchor,
    find_form,
    form_action,
    form_fields,
    hidden_fields,
    init_payload,
)


class TestFindAnchor:
    """`find_anchor` returns the first matching `<a>...</a>` substring."""

    def test_matches_by_href_and_text(self) -> None:
        html = (
            '<nav><a href="/a" class="x">Home</a><a href="/b" class="y">Docs</a></nav>'
        )
        assert find_anchor(html, href="/b", text="Docs") == (
            '<a href="/b" class="y">Docs</a>'
        )

    def test_matches_by_href_only(self) -> None:
        html = '<a href="/x">one</a><a href="/y">two</a>'
        assert find_anchor(html, href="/y") == '<a href="/y">two</a>'

    def test_matches_by_text_only(self) -> None:
        html = '<a href="/x">Alpha</a><a href="/y">Beta</a>'
        assert find_anchor(html, text="Beta") == '<a href="/y">Beta</a>'

    def test_returns_first_when_no_filters(self) -> None:
        html = '<a href="/x">one</a><a href="/y">two</a>'
        assert find_anchor(html) == '<a href="/x">one</a>'

    def test_single_quotes(self) -> None:
        html = "<a href='/q' class='z'>Quoted</a>"
        assert find_anchor(html, href="/q", text="Quoted") == (
            "<a href='/q' class='z'>Quoted</a>"
        )

    def test_nested_text_elements(self) -> None:
        html = '<a href="/n"><span class="icon"></span> Stats</a>'
        assert find_anchor(html, text="Stats") == (
            '<a href="/n"><span class="icon"></span> Stats</a>'
        )

    def test_text_is_substring_match(self) -> None:
        html = '<a href="/x">Hello, world</a>'
        assert find_anchor(html, text="world") == '<a href="/x">Hello, world</a>'

    def test_raises_when_nothing_matches_href(self) -> None:
        html = '<a href="/x">one</a>'
        with pytest.raises(LookupError, match=r"href='/missing'"):
            find_anchor(html, href="/missing")

    def test_raises_when_nothing_matches_text(self) -> None:
        html = '<a href="/x">one</a>'
        with pytest.raises(LookupError, match=r"text='nope'"):
            find_anchor(html, text="nope")

    def test_raises_when_no_anchors_at_all(self) -> None:
        with pytest.raises(LookupError, match="Anchor not found"):
            find_anchor("<div>no anchors here</div>", href="/x")


class TestAssertHasClass:
    """`assert_has_class` checks whitespace-separated class tokens."""

    def test_single_class(self) -> None:
        assert_has_class('<a class="alpha">x</a>', "alpha")

    def test_multi_class(self) -> None:
        assert_has_class('<a class="alpha beta gamma">x</a>', "beta")

    def test_missing_token_raises(self) -> None:
        with pytest.raises(AssertionError, match="alpha"):
            assert_has_class('<a class="beta">x</a>', "alpha")

    def test_no_class_attr_raises(self) -> None:
        with pytest.raises(AssertionError, match="no classes"):
            assert_has_class('<a href="/x">x</a>', "alpha")

    def test_empty_class_attr_raises(self) -> None:
        with pytest.raises(AssertionError, match="Expected class token"):
            assert_has_class('<a class="">x</a>', "alpha")

    def test_raises_on_fragment_without_tag(self) -> None:
        with pytest.raises(LookupError, match="does not contain a start tag"):
            assert_has_class("just text", "alpha")


class TestAssertMissingClass:
    """`assert_missing_class` is the inverse check."""

    def test_token_absent(self) -> None:
        assert_missing_class('<a class="alpha beta">x</a>', "gamma")

    def test_no_class_attr(self) -> None:
        assert_missing_class('<a href="/x">x</a>', "alpha")

    def test_token_present_raises(self) -> None:
        with pytest.raises(AssertionError, match="alpha"):
            assert_missing_class('<a class="alpha beta">x</a>', "alpha")


class TestFindForm:
    """`find_form` returns the first matching `<form>...</form>` substring."""

    def test_returns_first_when_no_filters(self) -> None:
        html = '<form action="/a"></form><form action="/b"></form>'
        assert find_form(html) == '<form action="/a"></form>'

    def test_matches_by_action(self) -> None:
        html = '<form action="/a"></form><form action="/b"></form>'
        assert find_form(html, action="/b") == '<form action="/b"></form>'

    def test_matches_by_contains(self) -> None:
        html = (
            '<form action="/a"><input name="wip_limit"></form>'
            '<form action="/b"><input name="title"></form>'
        )
        assert find_form(html, contains='name="title"') == (
            '<form action="/b"><input name="title"></form>'
        )

    def test_excludes_skips_matching_block(self) -> None:
        html = (
            '<form action="/a"><input name="title"><input name="wip_limit"></form>'
            '<form action="/b"><input name="title"></form>'
        )
        assert find_form(html, contains='name="title"', excludes="wip_limit") == (
            '<form action="/b"><input name="title"></form>'
        )

    def test_matches_multiline_block(self) -> None:
        html = '<form\n  action="/multi"\n  method="post">\n  <p>Body</p>\n</form>'
        assert find_form(html, action="/multi") == html

    def test_action_and_contains_combined(self) -> None:
        html = (
            '<form action="/a"><input name="one"></form>'
            '<form action="/a"><input name="two"></form>'
        )
        assert find_form(html, action="/a", contains='name="two"') == (
            '<form action="/a"><input name="two"></form>'
        )

    def test_raises_when_action_does_not_match(self) -> None:
        with pytest.raises(LookupError, match=r"action='/missing'"):
            find_form('<form action="/a"></form>', action="/missing")

    def test_raises_when_contains_does_not_match(self) -> None:
        with pytest.raises(LookupError, match=r"contains='nope'"):
            find_form('<form action="/a"></form>', contains="nope")

    def test_raises_when_excludes_rejects_every_form(self) -> None:
        with pytest.raises(LookupError, match=r"excludes='action'"):
            find_form('<form action="/a"></form>', excludes="action")

    def test_raises_when_no_forms_at_all(self) -> None:
        with pytest.raises(LookupError, match="Form not found"):
            find_form("<div>no forms here</div>")


class TestFormAction:
    """`form_action` reads the `action` attribute of the first tag."""

    def test_reads_action(self) -> None:
        assert form_action('<form action="/submit/" method="post"></form>') == (
            "/submit/"
        )

    def test_single_quotes(self) -> None:
        assert form_action("<form action='/q/'></form>") == "/q/"

    def test_empty_action(self) -> None:
        assert form_action('<form action=""></form>') == ""

    def test_raises_without_action(self) -> None:
        with pytest.raises(LookupError, match="no action attribute"):
            form_action('<form method="post"></form>')

    def test_raises_without_tag(self) -> None:
        with pytest.raises(LookupError, match="does not contain a start tag"):
            form_action("just text")


class TestFormFields:
    """`form_fields` maps every input name to its value."""

    def test_collects_every_input(self) -> None:
        block = (
            '<form action="/a">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="tok">'
            '<input type="text" name="title" value="Alpha">'
            "</form>"
        )
        assert form_fields(block) == {"csrfmiddlewaretoken": "tok", "title": "Alpha"}

    def test_missing_value_becomes_empty_string(self) -> None:
        assert form_fields('<input type="text" name="title">') == {"title": ""}

    def test_valueless_attribute_becomes_empty_string(self) -> None:
        assert form_fields('<input name="title" value>') == {"title": ""}

    def test_later_duplicate_wins(self) -> None:
        block = '<input name="step" value="1"><input name="step" value="2">'
        assert form_fields(block) == {"step": "2"}

    def test_ignores_unnamed_inputs_and_other_tags(self) -> None:
        block = (
            '<input type="submit" value="Save">'
            '<textarea name="body">x</textarea>'
            '<input name="title" value="Alpha">'
        )
        assert form_fields(block) == {"title": "Alpha"}

    def test_self_closing_input(self) -> None:
        assert form_fields('<input name="title" value="Alpha" />') == {"title": "Alpha"}

    def test_empty_fragment(self) -> None:
        assert form_fields("<form></form>") == {}


class TestHiddenFields:
    """`hidden_fields` keeps only the `type="hidden"` inputs."""

    def test_only_hidden_inputs(self) -> None:
        block = (
            '<input type="hidden" name="_next_form_origin" value="/page/">'
            '<input type="text" name="title" value="Alpha">'
        )
        assert hidden_fields(block) == {"_next_form_origin": "/page/"}

    def test_typeless_input_is_skipped(self) -> None:
        assert hidden_fields('<input name="title" value="Alpha">') == {}

    def test_hidden_without_value(self) -> None:
        assert hidden_fields('<input type="hidden" name="step">') == {"step": ""}


class TestInitPayload:
    """`init_payload` decodes the `Next._init({...})` bootstrap call."""

    def test_flat_payload(self) -> None:
        html = '<script>Next._init({"csrf_header": "X-CSRFToken"});</script>'
        assert init_payload(html) == {"csrf_header": "X-CSRFToken"}

    def test_nested_objects(self) -> None:
        html = '<script>Next._init({"a": {"b": {"c": 1}}, "d": [2, 3]});</script>'
        assert init_payload(html) == {"a": {"b": {"c": 1}}, "d": [2, 3]}

    def test_braces_inside_strings(self) -> None:
        html = 'Next._init({"tpl": "{unclosed", "other": "}"})'
        assert init_payload(html) == {"tpl": "{unclosed", "other": "}"}

    def test_escaped_quote_inside_string(self) -> None:
        html = 'Next._init({"quote": "a\\"}b"})'
        assert init_payload(html) == {"quote": 'a"}b'}

    def test_escaped_backslash_before_closing_quote(self) -> None:
        html = 'Next._init({"path": "c\\\\"})'
        assert init_payload(html) == {"path": "c\\"}

    def test_whitespace_before_payload(self) -> None:
        assert init_payload('Next._init(\n  {"a": 1}\n)') == {"a": 1}

    def test_escaped_markup_from_the_script_builder(self) -> None:
        html = '<script>Next._init({"tag": "\\u003Cscript\\u003E"});</script>'
        assert init_payload(html) == {"tag": "<script>"}

    def test_reads_the_first_call(self) -> None:
        html = 'Next._init({"a": 1});Next._init({"b": 2});'
        assert init_payload(html) == {"a": 1}

    def test_raises_when_call_is_absent(self) -> None:
        with pytest.raises(LookupError, match=r"Next\._init call not found"):
            init_payload("<div>no bootstrap here</div>")

    def test_raises_when_call_has_no_object(self) -> None:
        with pytest.raises(LookupError, match=r"Next\._init call not found"):
            init_payload("<script>Next._init(null);</script>")

    def test_raises_when_object_is_unterminated(self) -> None:
        with pytest.raises(LookupError, match="Unterminated object"):
            init_payload('<script>Next._init({"a": 1')
