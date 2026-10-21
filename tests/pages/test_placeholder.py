import pytest

from next.pages.placeholder import ROOT_MARKER, RootScan, scan_root


_BODY = "<html>{% template %}</html>"


class TestScanRoot:
    """`scan_root` decides whether a layout opens with its one root marker."""

    @pytest.mark.parametrize(
        "prefix",
        [
            "",
            "\n  \n",
            "{# a note #}",
            "{# one #}\n{# two #}",
            "{% load static %}",
            "{% load static %}\n{% load i18n %}",
            "{% comment %}{% layout root %}{% endcomment %}",
            "{% comment %}note{% endcomment %}\n",
            "{% load static %}{# note #}\n{% comment %}x{% endcomment %}  ",
        ],
        ids=[
            "bare",
            "blank_text",
            "brace_comment",
            "two_brace_comments",
            "load",
            "two_loads",
            "comment_block_holding_a_marker",
            "comment_block",
            "mixed_clean_prefix",
        ],
    )
    def test_a_clean_prefix_leaves_the_marker_in_place(self, prefix) -> None:
        content = f"{prefix}{ROOT_MARKER}{_BODY}"

        scan = scan_root(content)

        assert scan.is_root is True
        assert scan.markers == ((len(prefix), len(prefix) + len(ROOT_MARKER)),)

    @pytest.mark.parametrize(
        "prefix",
        [
            "<!doctype html>",
            "x",
            "<p>{{ title }}</p>",
            "{{ title }}",
            "{% if x %}{% endif %}",
            "{% block a %}{% endblock %}",
            "{% %}",
            "{% template %}",
        ],
        ids=[
            "doctype",
            "plain_text",
            "markup",
            "variable",
            "block_tag",
            "other_block",
            "empty_tag",
            "placeholder",
        ],
    )
    def test_a_dirty_prefix_keeps_the_marker_and_denies_the_root(self, prefix) -> None:
        content = f"{prefix}{ROOT_MARKER}{_BODY}"

        scan = scan_root(content)

        assert scan.is_root is False
        assert scan.markers == ((len(prefix), len(prefix) + len(ROOT_MARKER)),)

    def test_the_markers_hold_the_bounds_of_the_tag(self) -> None:
        content = f"{{# c #}}{ROOT_MARKER}{_BODY}"

        scan = scan_root(content)

        assert scan == RootScan(True, ((7, 7 + len(ROOT_MARKER)),))
        assert content[slice(*scan.markers[0])] == ROOT_MARKER

    def test_a_layout_without_a_marker_is_no_root(self) -> None:
        assert scan_root(_BODY) == RootScan(False, ())

    def test_an_empty_source_is_no_root(self) -> None:
        assert scan_root("") == RootScan(False, ())

    def test_a_marker_inside_a_comment_block_never_counts(self) -> None:
        content = f"{{% comment %}}{ROOT_MARKER}{{% endcomment %}}{_BODY}"

        assert scan_root(content) == RootScan(False, ())

    def test_an_unclosed_comment_block_swallows_the_marker(self) -> None:
        content = f"{{% comment %}}{ROOT_MARKER}{_BODY}"

        assert scan_root(content) == RootScan(False, ())

    def test_a_marker_after_a_closed_comment_block_counts(self) -> None:
        content = f"{{% comment %}}x{{% endcomment %}}{ROOT_MARKER}{_BODY}"

        assert scan_root(content).is_root is True

    def test_a_marker_inside_verbatim_is_text(self) -> None:
        content = f"{{% verbatim %}}{ROOT_MARKER}{{% endverbatim %}}{_BODY}"

        assert scan_root(content) == RootScan(False, ())

    def test_a_marker_inside_a_brace_comment_is_text(self) -> None:
        content = f"{{# {ROOT_MARKER} #}}{_BODY}"

        assert scan_root(content) == RootScan(False, ())

    def test_a_repeated_marker_denies_the_root_and_lists_both(self) -> None:
        content = f"{ROOT_MARKER}\n{ROOT_MARKER}{_BODY}"

        scan = scan_root(content)

        size = len(ROOT_MARKER)
        assert scan.is_root is False
        assert scan.markers == ((0, size), (size + 1, 2 * size + 1))

    def test_a_marker_after_a_misplaced_one_stays_listed(self) -> None:
        content = f"<p>x</p>{ROOT_MARKER}{ROOT_MARKER}"

        scan = scan_root(content)

        assert scan.is_root is False
        assert len(scan.markers) == 2

    def test_a_marker_with_more_words_is_no_marker(self) -> None:
        content = f"{{% layout root extra %}}{_BODY}"

        assert scan_root(content) == RootScan(False, ())

    def test_a_marker_naming_another_word_is_no_marker(self) -> None:
        content = f"{{% layout leaf %}}{_BODY}"

        assert scan_root(content) == RootScan(False, ())

    def test_a_padded_marker_is_a_marker(self) -> None:
        marker = "{%   layout    root   %}"
        content = f"{marker}{_BODY}"

        assert scan_root(content) == RootScan(True, ((0, len(marker)),))

    def test_a_multiline_marker_is_a_marker_under_the_widened_lexer(self) -> None:
        marker = "{% layout\n    root\n%}"
        content = f"{marker}{_BODY}"

        assert scan_root(content) == RootScan(True, ((0, len(marker)),))

    def test_the_marker_constant_is_a_root_on_its_own(self) -> None:
        assert scan_root(ROOT_MARKER) == RootScan(True, ((0, len(ROOT_MARKER)),))
