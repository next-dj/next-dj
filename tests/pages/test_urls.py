import pytest

from tests.support import file_router


class TestURLPatternParser:
    """``URLPatternParser`` turning bracket segments into Django path patterns."""

    @pytest.mark.parametrize(
        ("url_pattern", "expected_pattern", "expected_params"),
        [
            ("simple", "simple/", {}),
            ("user/[id]", "user/<str:id>/", {"id": "id"}),
            ("user/[int:user-id]", "user/<int:user_id>/", {"user_id": "user_id"}),
            ("user/[uuid:id]", "user/<uuid:id>/", {"id": "id"}),
            (
                "post/[slug:post-slug]",
                "post/<slug:post_slug>/",
                {"post_slug": "post_slug"},
            ),
            ("profile/[[args]]", "profile/<path:args>/", {"args": "args"}),
            (
                "user/[int:id]/posts/[[args]]",
                "user/<int:id>/posts/<path:args>/",
                {"id": "id", "args": "args"},
            ),
            ("", "", {}),
        ],
        ids=[
            "simple",
            "user_id",
            "user_int_id",
            "user_uuid_id",
            "post_slug",
            "profile_args",
            "user_id_posts_args",
            "empty",
        ],
    )
    def test_parse_url_pattern_variations(
        self, url_parser, url_pattern, expected_pattern, expected_params
    ) -> None:
        """Each bracket form maps to its Django converter and a normalised name."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)
        assert pattern == expected_pattern
        assert params == expected_params

    @pytest.mark.parametrize(
        ("url_pattern", "expected_contains"),
        [
            (
                "user/[int:user-id]/posts/[slug:post-slug]/[[args]]",
                [
                    "<int:user_id>",
                    "<slug:post_slug>",
                    "<path:args>",
                    "user_id",
                    "post_slug",
                    "args",
                ],
            )
        ],
        ids=["complex_pattern"],
    )
    def test_parse_url_pattern_complex(
        self, url_parser, url_pattern, expected_contains
    ) -> None:
        """Typed, slug, and catch-all segments coexist in one pattern."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)

        for expected in expected_contains:
            if expected.startswith("<"):
                assert expected in pattern
            else:
                assert expected in params

        assert pattern.endswith("/")

    @pytest.mark.parametrize(
        ("url_pattern", "pattern_contains", "params_condition"),
        [
            ("[]", ["["], lambda p: len(p) == 0 or "" in p),
            ("[[]]", ["["], lambda p: len(p) == 0 or "" in p),
        ],
        ids=["empty_bracket", "empty_double_bracket"],
    )
    def test_parse_url_pattern_edge_cases(
        self, url_parser, url_pattern, pattern_contains, params_condition
    ) -> None:
        """Malformed brackets pass through without raising."""
        pattern, params = url_parser.parse_url_pattern(url_pattern)
        assert any(contains in pattern for contains in pattern_contains)
        assert params_condition(params)

    @pytest.mark.parametrize(
        ("param_string", "expected_name", "expected_type"),
        [
            ("param", "param", "str"),
            ("int:user-id", "user-id", "int"),
            ("", "", "str"),
            ("   ", "", "str"),
            (":param", "param", ""),
        ],
        ids=["simple_param", "typed_param", "empty", "whitespace", "colon_prefix"],
    )
    def test_parse_param_name_and_type_variations(
        self, url_parser, param_string, expected_name, expected_type
    ) -> None:
        """A bare name defaults to ``str``, a leading colon leaves the type empty."""
        name, type_name = url_parser._parse_param_name_and_type(param_string)
        assert name == expected_name
        assert type_name == expected_type

    @pytest.mark.parametrize(
        ("url_path", "expected_params", "expected_pattern"),
        [
            (
                "user/[[profile]]/[int:user-id]/posts",
                ["profile", "user_id"],
                "user/<path:profile>/<int:user_id>/posts/",
            )
        ],
        ids=["args_and_params"],
    )
    def test_parse_url_pattern_with_args_and_params(
        self, url_parser, url_path, expected_params, expected_pattern
    ) -> None:
        """A catch-all segment and a typed segment resolve side by side."""
        django_pattern, parameters = url_parser.parse_url_pattern(url_path)

        for param in expected_params:
            assert param in parameters
        assert django_pattern == expected_pattern

    @pytest.mark.parametrize(
        ("url_path", "expected_name"),
        [
            ("user/[int:user-id]/posts", "user_int_user_id_posts"),
            ("profile/[[args]]", "profile_args"),
            (
                "user/[int:id]/posts/[slug:post-slug]/[[args]]",
                "user_int_id_posts_slug_post_slug_args",
            ),
        ],
        ids=["int_param", "path_param", "mixed_params"],
    )
    def test_prepare_url_name_with_colons(
        self, url_parser, url_path, expected_name
    ) -> None:
        """The route name flattens brackets and colons into a reversible identifier."""
        clean_name = url_parser.prepare_url_name(url_path)
        assert clean_name == expected_name
        assert ":" not in clean_name

    def test_scan_pages_directory_virtual_view_detection(self, tmp_path) -> None:
        """A lone ``template.djx`` routes to a synthesised ``page.py``."""
        backend = file_router()

        virtual_dir = tmp_path / "virtual"
        virtual_dir.mkdir()
        template_file = virtual_dir / "template.djx"
        template_file.write_text("<h1>Virtual Page</h1>")

        results = list(backend._scan_pages_directory(tmp_path))

        assert len(results) == 1
        url_path, page_path = results[0]
        assert url_path == "virtual"
        assert page_path == virtual_dir / "page.py"
