from pathlib import Path

import pytest

from next.urls import DuplicateURLParameterError, URLPatternParser


class TestParseUrlPatternHappyPath:
    """Single-pass bracket conversion keeps the pre-callback behaviour."""

    @pytest.mark.parametrize(
        ("url_path", "expected_pattern", "expected_params"),
        [
            ("", "", {}),
            ("about", "about/", {}),
            ("about/", "about/", {}),
            ("user/[name]", "user/<str:name>/", {"name": "name"}),
            ("user/[int:id]", "user/<int:id>/", {"id": "id"}),
            (
                "post/[slug:post-slug]",
                "post/<slug:post_slug>/",
                {"post_slug": "post_slug"},
            ),
            ("item/[uuid:pk]", "item/<uuid:pk>/", {"pk": "pk"}),
            ("files/[[args]]", "files/<path:args>/", {"args": "args"}),
            ("files/[[args]]/", "files/<path:args>/", {"args": "args"}),
            (
                "docs/[[doc-path]]",
                "docs/<path:doc_path>/",
                {"doc_path": "doc_path"},
            ),
            (
                "user/[int:id]/files/[[rest]]",
                "user/<int:id>/files/<path:rest>/",
                {"id": "id", "rest": "rest"},
            ),
        ],
        ids=[
            "empty",
            "plain",
            "plain_trailing_slash",
            "str_param",
            "int_param",
            "slug_param_hyphen",
            "uuid_param",
            "single_wildcard",
            "wildcard_trailing_slash",
            "wildcard_hyphen",
            "param_then_wildcard",
        ],
    )
    def test_parse_url_pattern(
        self,
        url_parser,
        url_path,
        expected_pattern,
        expected_params,
    ) -> None:
        """Each converter kind maps to its Django path syntax unchanged."""
        pattern, params = url_parser.parse_url_pattern(url_path)
        assert pattern == expected_pattern
        assert params == expected_params

    def test_prepare_url_name_is_injective_on_happy_paths(self, url_parser) -> None:
        """Distinct routable paths keep distinct reverse names."""
        url_paths = [
            "simple",
            "user/[id]",
            "user/[int:id]",
            "user/[int:id]/posts",
            "post/[slug:post-slug]",
            "profile/[[args]]",
        ]
        names = [url_parser.prepare_url_name(url_path) for url_path in url_paths]
        assert len(set(names)) == len(names)


class TestParseUrlPatternDuplicates:
    """Conflicting bracket names fail at pattern build time."""

    @pytest.mark.parametrize(
        ("url_path", "expected_param_name"),
        [
            ("a/[[x]]/b/[[y]]", "y"),
            ("[[a]]/[[b]]", "b"),
            ("[a-b]/[a_b]", "a_b"),
            ("[x]/[int:x]", "x"),
            ("[[x]]/[x]", "x"),
            ("[x]/[[x]]", "x"),
        ],
        ids=[
            "second_wildcard_apart",
            "second_wildcard_adjacent",
            "hyphen_underscore_collision",
            "repeated_param",
            "wildcard_then_param",
            "param_then_wildcard",
        ],
    )
    def test_duplicate_names_raise(
        self,
        url_parser,
        url_path,
        expected_param_name,
    ) -> None:
        """Any repeat of a normalised name, wildcard included, raises."""
        with pytest.raises(DuplicateURLParameterError) as excinfo:
            url_parser.parse_url_pattern(url_path)
        error = excinfo.value
        assert error.param_name == expected_param_name
        assert error.url_path == url_path
        assert error.file_path is None
        assert expected_param_name in str(error)
        assert url_path in str(error)
        assert "Page file" not in str(error)


class TestDuplicateParameterNames:
    """`duplicate_parameter_names` lists every repeated normalised name."""

    @pytest.mark.parametrize(
        ("url_path", "expected"),
        [
            ("a/[id]/[int:id]/[slug]/[slug]", ["id", "slug"]),
            ("[user-id]/[user_id]", ["user_id"]),
            ("[[x]]/[x]", ["x"]),
            ("[x]/[[x]]", ["x"]),
            ("[[a]]/[[b]]", []),
            ("user/[id]/post/[slug]", []),
            ("[x]/[x]/[x]", ["x"]),
        ],
        ids=[
            "two_independent_duplicates",
            "dash_underscore_collision",
            "wildcard_then_param",
            "param_then_wildcard",
            "distinct_wildcards_are_clean",
            "no_duplicates",
            "triple_repeat_listed_once",
        ],
    )
    def test_duplicate_parameter_names(self, url_parser, url_path, expected) -> None:
        """Duplicates appear once each, in first-repeat order, wild and param alike."""
        assert url_parser.duplicate_parameter_names(url_path) == expected


class TestDuplicateURLParameterError:
    """Error attributes and optional file context."""

    def test_is_value_error(self) -> None:
        """The error stays catchable as ValueError for older call sites."""
        error = DuplicateURLParameterError("x", "[x]/[x]")
        assert isinstance(error, ValueError)

    def test_message_includes_file_path_when_given(self) -> None:
        """A known page file lands in the message tail."""
        file_path = Path("/pages/dup/page.py")
        error = DuplicateURLParameterError("x", "[x]/[x]", file_path=file_path)
        assert error.file_path == file_path
        assert "Page file: /pages/dup/page.py." in str(error)

    def test_classvar_points_at_public_error(self) -> None:
        """Parser exposes the error class for import-cycle-free callers."""
        assert URLPatternParser.duplicate_parameter_error is DuplicateURLParameterError


class TestCreateUrlPatternFileContext:
    """Page.create_url_pattern re-raises with the page file attached."""

    def test_reraises_with_file_path(self, page_instance, url_parser, tmp_path) -> None:
        """The wrapped error names the page file and chains the original."""
        page_file = tmp_path / "page.py"
        with pytest.raises(DuplicateURLParameterError) as excinfo:
            page_instance.create_url_pattern("[a-b]/[a_b]", page_file, url_parser)
        error = excinfo.value
        assert error.param_name == "a_b"
        assert error.url_path == "[a-b]/[a_b]"
        assert error.file_path == page_file
        assert str(page_file) in str(error)
        assert isinstance(error.__cause__, DuplicateURLParameterError)
        assert error.__cause__.file_path is None


class TestGeneratePatternsFileContext:
    """FileRouterBackend re-raises scan failures with the page file attached."""

    def test_keeps_file_path_from_create_url_pattern(self, router, tmp_path) -> None:
        """A real duplicate-wildcard tree reports the discovered page.py."""
        page_dir = tmp_path / "[[x]]" / "[[y]]"
        page_dir.mkdir(parents=True)
        page_file = page_dir / "page.py"
        page_file.write_text("def render(request, **kwargs):\n    return 'ok'\n")

        with pytest.raises(DuplicateURLParameterError) as excinfo:
            list(router._generate_patterns_from_directory(tmp_path))
        error = excinfo.value
        assert error.param_name == "y"
        assert error.file_path == page_file
        assert str(page_file) in str(error)
