from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.test import override_settings

from next.permissions import ObjectPermissionBackend, add_perm, predicate
from next.permissions.checks import (
    check_has_perm_names,
    check_permission_backend,
    check_permission_predicates,
)
from tests.pages.checks.metadata.trees import (
    component_template,
    framework,
    layout_root,
    templated_page,
)
from tests.support import (
    MODEL_BACKEND,
    OBJECT_PERMISSION_BACKENDS,
    check_ids,
    patch_checks_router_manager,
)


pytestmark = pytest.mark.usefixtures("isolated_permissions")


class SubclassedBackend(ObjectPermissionBackend):
    """A project backend that extends the predicate backend."""


def two_params(user, obj):
    return True


def no_params():
    return True


def user_only(user):
    return True


def defaulted_obj(user, obj=None):
    return True


def with_kwargs(user, obj, **kwargs):
    return True


def optional_keyword(user, *, strict=False):
    return True


def var_positional(*args):
    return True


def three_params(user, obj, extra):
    return True


def keyword_only(user, *, obj):
    return True


class TestPredicateSignatures:
    """`next.E161` names every leaf whose signature a predicate cannot call."""

    @pytest.mark.parametrize(
        "test",
        [
            no_params,
            user_only,
            two_params,
            defaulted_obj,
            with_kwargs,
            optional_keyword,
        ],
    )
    def test_a_callable_leaf_is_silent(self, test) -> None:
        add_perm("blog.change_post", test)
        assert check_permission_predicates() == []

    @pytest.mark.parametrize(
        ("test", "problem"),
        [
            pytest.param(three_params, "takes 3 positional parameters", id="three"),
            pytest.param(var_positional, "takes *args", id="var-positional"),
            pytest.param(
                keyword_only,
                "requires the keyword-only parameter 'obj'",
                id="required-keyword-only",
            ),
            pytest.param(max, "has no signature Python can read", id="no-signature"),
        ],
    )
    def test_a_leaf_with_the_wrong_signature_errors(self, test, problem: str) -> None:
        add_perm("blog.change_post", test)
        messages = check_permission_predicates()
        assert check_ids(messages) == ["next.E161"]
        assert messages[0].obj == "blog.change_post"
        assert repr(test.__name__) in messages[0].msg
        assert problem in messages[0].msg
        assert "**kwargs is allowed" in messages[0].hint

    def test_only_the_bad_leaf_of_a_composite_is_named(self) -> None:
        rule = predicate(no_params) & ~(predicate(user_only) | predicate(three_params))
        add_perm("blog.change_post", rule)
        add_perm("blog.view_post", two_params)
        messages = check_permission_predicates()
        assert check_ids(messages) == ["next.E161"]
        assert "'three_params'" in messages[0].msg
        assert messages[0].obj == "blog.change_post"

    def test_every_bad_leaf_is_named(self) -> None:
        add_perm(
            "blog.change_post", predicate(three_params) | predicate(var_positional)
        )
        messages = check_permission_predicates()
        assert check_ids(messages) == ["next.E161", "next.E161"]
        assert "'three_params'" in messages[0].msg
        assert "'var_positional'" in messages[1].msg

    def test_nothing_registered_is_silent(self) -> None:
        assert check_permission_predicates() == []


class TestBackendListed:
    """`next.E162` fires when permissions are registered but no backend asks them."""

    def test_registered_without_the_backend_errors(self) -> None:
        add_perm("blog.change_post", two_params)
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND]):
            messages = check_permission_backend()
        assert check_ids(messages) == ["next.E162"]
        assert messages[0].obj is django_settings
        assert "next.permissions.ObjectPermissionBackend" in messages[0].msg
        assert "login(request, user, backend=" in messages[0].hint

    @pytest.mark.parametrize(
        "backends",
        [
            pytest.param(OBJECT_PERMISSION_BACKENDS, id="the-backend"),
            pytest.param(
                [MODEL_BACKEND, f"{__name__}.SubclassedBackend"], id="a-subclass"
            ),
            pytest.param(
                ["nowhere.Backend", "next.permissions.ObjectPermissionBackend"],
                id="after-an-unimportable-entry",
            ),
        ],
    )
    def test_a_listed_backend_is_silent(self, backends: list[str]) -> None:
        add_perm("blog.change_post", two_params)
        with override_settings(AUTHENTICATION_BACKENDS=backends):
            assert check_permission_backend() == []

    @pytest.mark.parametrize(
        "backends",
        [
            pytest.param(["nowhere.Backend"], id="unimportable"),
            pytest.param(["os.path.join"], id="not-a-class"),
            pytest.param([f"{__name__}.two_params"], id="a-function"),
        ],
    )
    def test_an_entry_that_is_no_backend_class_errors(
        self, backends: list[str]
    ) -> None:
        add_perm("blog.change_post", two_params)
        with override_settings(AUTHENTICATION_BACKENDS=backends):
            assert check_ids(check_permission_backend()) == ["next.E162"]

    def test_nothing_registered_is_silent(self) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND]):
            assert check_permission_backend() == []


def _tag(perm: str) -> str:
    return f"{{% has_perm {perm} %}}yes{{% else %}}no{{% endhas_perm %}}"


class TestHasPermNames:
    """`next.W130` names a literal `{% has_perm %}` nothing can grant."""

    @pytest.fixture()
    def pages(self, tmp_path: Path) -> Path:
        return layout_root(tmp_path)

    def test_an_unknown_name_in_a_page_template_warns(self, pages: Path) -> None:
        page_file = templated_page(pages / "hello", "x = 1\n", _tag("'blog.nope'"))
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            messages = check_has_perm_names()
        assert check_ids(messages) == ["next.W130"]
        assert messages[0].obj == str(page_file)
        assert "'blog.nope'" in messages[0].msg

    def test_an_unknown_name_in_a_component_template_warns(self, pages: Path) -> None:
        component = component_template(pages, "badge", _tag("'blog.nope'"))
        templated_page(pages / "hello", "x = 1\n", "<p>no tag</p>")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            messages = check_has_perm_names()
        assert check_ids(messages) == ["next.W130"]
        assert messages[0].obj == str(component)

    def test_a_component_a_page_reaches_is_named_once(self, pages: Path) -> None:
        component_template(pages, "badge", _tag("'blog.nope'"))
        page_file = templated_page(
            pages / "hello", "x = 1\n", '{% component "badge" %}'
        )
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            messages = check_has_perm_names()
        assert check_ids(messages) == ["next.W130"]
        assert messages[0].obj == str(page_file)

    def test_one_warning_per_unknown_name(self, pages: Path) -> None:
        body = _tag("'blog.nope'") + _tag("'blog.nope'") + _tag("'shop.nope'")
        templated_page(pages / "hello", "x = 1\n", body)
        templated_page(pages / "other", "x = 1\n", _tag("'blog.nope'"))
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            messages = check_has_perm_names()
        assert sorted(m.msg.split()[2] for m in messages) == [
            "'blog.nope'",
            "'shop.nope'",
        ]

    @pytest.mark.parametrize(
        "perm",
        [
            pytest.param("'auth.add_user'", id="model-permission"),
            pytest.param("'blog.change_post'", id="registered"),
            pytest.param("perm", id="variable"),
            pytest.param("'blog.nope'|lower", id="filtered"),
        ],
    )
    def test_a_name_something_grants_is_silent(self, pages: Path, perm: str) -> None:
        add_perm("blog.change_post", two_params)
        component_template(pages, "badge", _tag(perm))
        templated_page(pages / "hello", "x = 1\n", _tag(perm))
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_has_perm_names() == []

    def test_a_component_that_does_not_compile_is_skipped(self, pages: Path) -> None:
        component_template(pages, "broken", "{% has_perm %}")
        templated_page(pages / "hello", "x = 1\n", "<p>x</p>")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_has_perm_names() == []

    def test_a_component_without_a_template_is_skipped(self, pages: Path) -> None:
        folder = pages / "_components" / "bare"
        folder.mkdir(parents=True)
        (folder / "component.py").write_text("x = 1\n")
        templated_page(pages / "hello", "x = 1\n", _tag("'blog.nope'"))
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_ids(check_has_perm_names()) == ["next.W130"]

    @pytest.mark.parametrize("backends", [[], "not-a-list"])
    def test_no_component_backends_reads_pages_only(
        self, pages: Path, backends: object
    ) -> None:
        templated_page(pages / "hello", "x = 1\n", _tag("'blog.nope'"))
        with (
            override_settings(NEXT_FRAMEWORK={"COMPONENT_BACKENDS": backends}),
            patch_checks_router_manager(pages_directory=pages),
        ):
            assert check_ids(check_has_perm_names()) == ["next.W130"]
