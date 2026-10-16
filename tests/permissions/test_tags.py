from pathlib import Path

import pytest
from django.http import HttpRequest
from django.template import (
    Context,
    Engine,
    RequestContext,
    Template,
    TemplateSyntaxError,
)

from next.components import render_component
from next.pages import page
from next.permissions.nodes import HasPermNode
from tests.support import (
    PermissionHolder,
    build_zone_request,
    component_info,
    unified_view,
    write_page,
)


_GRANTED = "{% has_perm 'blog.change_post' %}yes{% else %}no{% endhas_perm %}"
_WITH_OBJECT = "{% has_perm 'blog.change_post' post %}yes{% else %}no{% endhas_perm %}"


def _request(user: object) -> HttpRequest:
    request = HttpRequest()
    request.user = user
    return request


def _render(source: str, **context: object) -> str:
    return Template(source).render(Context(context))


class TestBranches:
    """The tag renders the first branch for a holder and the else branch otherwise."""

    @pytest.mark.parametrize(
        ("held", "expected"),
        [
            pytest.param(("blog.change_post",), "yes", id="granted"),
            pytest.param((), "no", id="denied"),
        ],
    )
    def test_with_an_else_branch(self, held: tuple[str, ...], expected: str) -> None:
        assert _render(_GRANTED, user=PermissionHolder(*held)) == expected

    @pytest.mark.parametrize(
        ("held", "expected"),
        [
            pytest.param(("blog.change_post",), "yes", id="granted"),
            pytest.param((), "", id="denied"),
        ],
    )
    def test_without_an_else_branch(self, held: tuple[str, ...], expected: str) -> None:
        source = "{% has_perm 'blog.change_post' %}yes{% endhas_perm %}"
        assert _render(source, user=PermissionHolder(*held)) == expected

    def test_a_bare_tag_asks_without_an_object(self) -> None:
        user = PermissionHolder("blog.change_post")
        _render(_GRANTED, user=user)
        assert user.asked == [("blog.change_post",)]

    def test_the_branches_render_in_the_outer_context(self) -> None:
        source = "{% has_perm 'blog.change_post' %}{{ title }}{% endhas_perm %}"
        assert (
            _render(source, user=PermissionHolder("blog.change_post"), title="Hi")
            == "Hi"
        )

    def test_a_multiline_tag(self) -> None:
        source = (
            "{% has_perm\n    'blog.change_post'\n    post\n%}yes{% else %}no"
            "{% endhas_perm %}"
        )
        user = PermissionHolder("blog.change_post")
        post = object()
        assert _render(source, user=user, post=post) == "yes"
        assert user.asked == [("blog.change_post", post)]


class TestObject:
    """An object the tag names reaches `has_perm`, a missing one denies."""

    def test_the_object_reaches_has_perm(self) -> None:
        user = PermissionHolder("blog.change_post")
        post = object()
        assert _render(_WITH_OBJECT, user=user, post=post) == "yes"
        assert user.asked == [("blog.change_post", post)]

    def test_a_refused_object_renders_else(self) -> None:
        user = PermissionHolder()
        assert _render(_WITH_OBJECT, user=user, post=object()) == "no"

    @pytest.mark.parametrize(
        "context",
        [
            pytest.param({}, id="missing-variable"),
            pytest.param({"post": None}, id="none"),
        ],
    )
    def test_no_object_renders_else_without_asking(self, context) -> None:
        user = PermissionHolder("blog.change_post")
        assert _render(_WITH_OBJECT, user=user, **context) == "no"
        assert user.asked == []

    def test_an_object_through_a_filter(self) -> None:
        source = (
            "{% has_perm 'blog.change_post' posts|first %}yes{% else %}no"
            "{% endhas_perm %}"
        )
        user = PermissionHolder("blog.change_post")
        post = object()
        assert _render(source, user=user, posts=[post]) == "yes"
        assert user.asked == [("blog.change_post", post)]


class TestPermissionExpression:
    """The permission may be a variable, anything but a non-empty string denies."""

    def test_a_variable_permission(self) -> None:
        source = "{% has_perm perm %}yes{% else %}no{% endhas_perm %}"
        user = PermissionHolder("blog.change_post")
        assert _render(source, user=user, perm="blog.change_post") == "yes"
        assert _render(source, user=user, perm="blog.delete_post") == "no"

    @pytest.mark.parametrize(
        "context",
        [
            pytest.param({}, id="missing"),
            pytest.param({"perm": ""}, id="empty"),
            pytest.param({"perm": 7}, id="int"),
            pytest.param({"perm": None}, id="none"),
        ],
    )
    def test_a_non_string_or_empty_permission_denies(self, context) -> None:
        source = "{% has_perm perm %}yes{% else %}no{% endhas_perm %}"
        user = PermissionHolder("")
        assert _render(source, user=user, **context) == "no"
        assert user.asked == []

    def test_a_missing_variable_denies_whatever_the_engine_renders_for_it(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(Engine.get_default(), "string_if_invalid", "blog.view_post")
        source = "{% has_perm perm %}yes{% else %}no{% endhas_perm %}"
        user = PermissionHolder("blog.view_post")
        assert _render(source, user=user) == "no"
        assert user.asked == []


class TestUserSource:
    """The user comes off the request first and the `user` key last."""

    def test_from_a_request_context(self) -> None:
        template = Template(_GRANTED)
        request = _request(PermissionHolder("blog.change_post"))
        assert (
            template.render(RequestContext(request, {"user": PermissionHolder()}))
            == "yes"
        )

    def test_from_the_request_key(self) -> None:
        request = _request(PermissionHolder("blog.change_post"))
        assert _render(_GRANTED, request=request, user=PermissionHolder()) == "yes"

    def test_a_request_without_a_user_falls_back_to_the_user_key(self) -> None:
        user = PermissionHolder("blog.change_post")
        assert _render(_GRANTED, request=HttpRequest(), user=user) == "yes"

    def test_from_the_user_key(self) -> None:
        assert _render(_GRANTED, user=PermissionHolder("blog.change_post")) == "yes"

    def test_no_user_renders_else(self) -> None:
        assert _render(_GRANTED) == "no"

    def test_a_user_without_has_perm_renders_else(self) -> None:
        assert _render(_GRANTED, user=object()) == "no"

    def test_a_non_callable_has_perm_renders_else(self) -> None:
        user = type("_Odd", (), {"has_perm": True})()
        assert _render(_GRANTED, user=user) == "no"


class TestSyntax:
    """The tag takes a permission and an optional object, nothing else."""

    @pytest.mark.parametrize(
        "bits",
        [
            pytest.param("", id="one-bit"),
            pytest.param(" 'blog.change_post' post extra", id="four-bits"),
        ],
    )
    def test_wrong_arity_raises(self, bits: str) -> None:
        with pytest.raises(TemplateSyntaxError, match="has_perm takes a permission"):
            Template(f"{{% has_perm{bits} %}}x{{% endhas_perm %}}")

    def test_an_unclosed_tag_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template("{% has_perm 'blog.change_post' %}x")

    def test_a_tag_after_else_other_than_the_end_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template("{% has_perm 'a.b' %}x{% else %}y{% else %}z{% endhas_perm %}")


class TestLiteralPerm:
    """`literal_perm` names a permission only when the tag spells it out."""

    @pytest.mark.parametrize(
        ("bit", "expected"),
        [
            pytest.param("'blog.change_post'", "blog.change_post", id="single-quoted"),
            pytest.param('"blog.change_post"', "blog.change_post", id="double-quoted"),
            pytest.param("perm", None, id="variable"),
            pytest.param("'blog.change_post'|lower", None, id="filtered"),
            pytest.param("perm|default:'a.b'", None, id="filtered-variable"),
        ],
    )
    def test_literal_perm(self, bit: str, expected: str | None) -> None:
        template = Template(f"{{% has_perm {bit} %}}x{{% endhas_perm %}}")
        (node,) = template.nodelist.get_nodes_by_type(HasPermNode)
        assert node.literal_perm() == expected

    def test_both_branches_are_child_nodelists(self) -> None:
        template = Template(_GRANTED)
        (node,) = template.nodelist.get_nodes_by_type(HasPermNode)
        assert [str(n.s) for n in node.nodelist_true] == ["yes"]
        assert [str(n.s) for n in node.nodelist_false] == ["no"]
        assert node.child_nodelists == ("nodelist_true", "nodelist_false")


class TestRenderPaths:
    """The tag finds the user in a component and in a zone rendered on its own."""

    @pytest.mark.parametrize(
        ("held", "expected"),
        [
            pytest.param(("blog.change_post",), "yes", id="granted"),
            pytest.param((), "no", id="denied"),
        ],
    )
    def test_inside_a_component_template(
        self, tmp_path: Path, held: tuple[str, ...], expected: str
    ) -> None:
        folder = tmp_path / "badge"
        folder.mkdir()
        info = component_info(folder, name="badge", template=_GRANTED, is_simple=True)
        html = render_component(info, {}, request=_request(PermissionHolder(*held)))
        assert html.strip() == expected

    @pytest.mark.parametrize(
        ("held", "expected"),
        [
            pytest.param(("blog.change_post",), b"yes", id="granted"),
            pytest.param((), b"no", id="denied"),
        ],
    )
    def test_inside_a_zone_rendered_alone(
        self, tmp_path: Path, held: tuple[str, ...], expected: bytes
    ) -> None:
        body = f'<p>outside</p>{{% zone "z" %}}<b>{_GRANTED}</b>{{% endzone %}}'
        leaf = write_page(tmp_path, "zoned", "x = 1\n", body=body)
        request = build_zone_request("z")
        request.user = PermissionHolder(*held)
        response = unified_view(page, leaf)(request)
        assert response.status_code == 200
        assert b"<b>" + expected + b"</b>" in response.content
        assert b"outside" not in response.content
