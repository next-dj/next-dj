import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import override_settings

from next.components.backends import FileComponentsBackend
from next.forms import Form, action
from next.pages.checks import access as access_checks
from next.permissions import ObjectPermissionBackend, add_perm
from next.permissions.checks import (
    check_permission_backend,
    check_permission_predicates,
    check_permissions_known,
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
    next_framework_settings_stand_in,
    patch_checks_router_manager,
    routed,
    write_page,
)
from tests.support.access import COMPONENTS, page_source, write_proxy


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
    """`next.E159` names every predicate whose function it cannot call."""

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
    def test_a_callable_test_is_silent(self, test) -> None:
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
    def test_a_test_with_the_wrong_signature_errors(self, test, problem: str) -> None:
        add_perm("blog.change_post", test)
        messages = check_permission_predicates()
        assert check_ids(messages) == ["next.E159"]
        assert messages[0].obj == "blog.change_post"
        assert repr(test.__name__) in messages[0].msg
        assert problem in messages[0].msg
        assert "**kwargs is allowed" in messages[0].hint

    def test_every_bad_predicate_is_named(self) -> None:
        add_perm("blog.change_post", three_params)
        add_perm("blog.view_post", two_params)
        add_perm("blog.delete_post", var_positional)
        messages = check_permission_predicates()
        assert check_ids(messages) == ["next.E159", "next.E159"]
        assert [m.obj for m in messages] == ["blog.change_post", "blog.delete_post"]

    def test_nothing_registered_is_silent(self) -> None:
        assert check_permission_predicates() == []


class TestBackendListed:
    """`next.E160` fires when permissions are registered but no backend asks them."""

    def test_registered_without_the_backend_errors(self) -> None:
        add_perm("blog.change_post", two_params)
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND]):
            messages = check_permission_backend()
        assert check_ids(messages) == ["next.E160"]
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
            assert check_ids(check_permission_backend()) == ["next.E160"]

    def test_nothing_registered_is_silent(self) -> None:
        with override_settings(AUTHENTICATION_BACKENDS=[MODEL_BACKEND]):
            assert check_permission_backend() == []


def _tag(perm: str) -> str:
    return f"{{% has_perm {perm} %}}yes{{% else %}}no{{% endhas_perm %}}"


def _warned(head: str) -> list[tuple[object, str]]:
    """Return the `obj` and the permission of each W127 whose text opens with `head`."""
    found: list[tuple[object, str]] = []
    for message in check_permissions_known():
        assert message.id == "next.W127"
        if message.msg.startswith(head):
            found.append((message.obj, _UNKNOWN.search(message.msg)[1]))
    return found


_UNKNOWN = re.compile(r"names the permission '([^']+)'")

_TEMPLATE = "{% has_perm %}"

_DECLARED = "permission_required()"


class TestPermissionsKnownInTemplates:
    """`next.W127` names a literal `{% has_perm %}` nothing can grant."""

    @pytest.fixture()
    def pages(self, tmp_path: Path) -> Path:
        return layout_root(tmp_path)

    def test_an_unknown_name_in_a_page_template_warns(self, pages: Path) -> None:
        page_file = templated_page(pages / "hello", "x = 1\n", _tag("'blog.nope'"))
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            (message,) = (
                m for m in check_permissions_known() if m.msg.startswith(_TEMPLATE)
            )
        assert message.obj == str(page_file)
        assert message.msg == (
            f"{{% has_perm %}} in {page_file} names the permission 'blog.nope', which "
            "no installed model defines and no add_perm() registers, so every user "
            "but a superuser is refused it."
        )
        assert message.hint == (
            "Fix the name, or register the permission with next.permissions.add_perm()."
        )

    def test_an_unknown_name_in_a_component_template_warns(self, pages: Path) -> None:
        component = component_template(pages, "badge", _tag("'blog.nope'"))
        templated_page(pages / "hello", "x = 1\n", "<p>no tag</p>")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert _warned(_TEMPLATE) == [(str(component), "blog.nope")]

    def test_a_component_a_page_reaches_is_named_once(self, pages: Path) -> None:
        component_template(pages, "badge", _tag("'blog.nope'"))
        page_file = templated_page(
            pages / "hello", "x = 1\n", '{% component "badge" %}'
        )
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert _warned(_TEMPLATE) == [(str(page_file), "blog.nope")]

    def test_one_warning_per_unknown_name(self, pages: Path) -> None:
        body = _tag("'blog.nope'") + _tag("'blog.nope'") + _tag("'shop.nope'")
        templated_page(pages / "hello", "x = 1\n", body)
        templated_page(pages / "other", "x = 1\n", _tag("'blog.nope'"))
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert sorted(perm for _obj, perm in _warned(_TEMPLATE)) == [
                "blog.nope",
                "shop.nope",
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
            assert _warned(_TEMPLATE) == []

    def test_a_component_that_does_not_compile_is_skipped(self, pages: Path) -> None:
        component_template(pages, "broken", "{% has_perm %}")
        templated_page(pages / "hello", "x = 1\n", "<p>x</p>")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert _warned(_TEMPLATE) == []

    def test_a_component_without_a_template_is_skipped(self, pages: Path) -> None:
        folder = pages / "_components" / "bare"
        folder.mkdir(parents=True)
        (folder / "component.py").write_text("x = 1\n")
        page_file = templated_page(pages / "hello", "x = 1\n", _tag("'blog.nope'"))
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert _warned(_TEMPLATE) == [(str(page_file), "blog.nope")]

    @pytest.mark.parametrize("backends", [[], "not-a-list"])
    def test_no_component_backends_reads_pages_only(
        self, pages: Path, backends: object
    ) -> None:
        page_file = templated_page(pages / "hello", "x = 1\n", _tag("'blog.nope'"))
        with (
            override_settings(NEXT_FRAMEWORK={"COMPONENT_BACKENDS": backends}),
            patch_checks_router_manager(pages_directory=pages),
        ):
            assert _warned(_TEMPLATE) == [(str(page_file), "blog.nope")]


@pytest.mark.usefixtures("isolated_declarations")
class TestPermissionsKnownInDeclarations:
    """`next.W127` names each file declaring a permission nothing grants."""

    @pytest.fixture()
    def project(self, tmp_path) -> Path:
        root = tmp_path / "pages"
        write_proxy(root, 'proxy.permission_required("auth.no_such_proxy")\n')
        write_page(
            root,
            "a",
            page_source(
                'page.permission_required(("auth.view_group", "auth.no_such_page"))\n'
            ),
        )
        card = root / "_components" / "card"
        card.mkdir(parents=True)
        (card / "component.py").write_text(
            "from next.components import component\n\n"
            'component.permission_required("auth.no_such_card")\n'
        )
        (card / "component.djx").write_text("card")
        return root

    def test_every_declaring_file_is_named(self, project) -> None:
        with routed(project, COMPONENT_BACKENDS=COMPONENTS):
            warned = _warned(_DECLARED)

        assert sorted(warned) == [
            (
                str(project / "_components" / "card" / "component.py"),
                "auth.no_such_card",
            ),
            (str(project / "a" / "page.py"), "auth.no_such_page"),
            (str(project / "proxy.py"), "auth.no_such_proxy"),
        ]

    def test_the_message_names_the_file(self, project) -> None:
        with routed(project, COMPONENT_BACKENDS=COMPONENTS):
            messages = [
                m
                for m in check_permissions_known()
                if m.obj == str(project / "proxy.py")
            ]

        assert [m.msg.split(" names ")[0] for m in messages] == [
            f"permission_required() in {project / 'proxy.py'}"
        ]

    def test_a_registered_permission_is_silent(self, project) -> None:
        for perm in ("no_such_proxy", "no_such_page", "no_such_card"):
            add_perm(f"auth.{perm}", no_params)

        with routed(project, COMPONENT_BACKENDS=COMPONENTS):
            assert _warned(_DECLARED) == []

    @pytest.mark.parametrize(
        ("backends", "imported"), [(COMPONENTS, True), (None, False)]
    )
    def test_component_modules_load_only_under_listed_backends(
        self, project, monkeypatch, backends, imported
    ) -> None:
        monkeypatch.setattr(
            access_checks,
            "next_framework_settings",
            next_framework_settings_stand_in(COMPONENT_BACKENDS=backends),
        )
        with (
            routed(project, COMPONENT_BACKENDS=COMPONENTS),
            patch.object(
                FileComponentsBackend, "import_component_modules", return_value=()
            ) as loads,
        ):
            warned = _warned(_DECLARED)

        assert loads.called is imported
        assert {perm for _obj, perm in warned} >= {
            "auth.no_such_page",
            "auth.no_such_proxy",
        }

    def test_without_contrib_auth_it_is_silent(self, project) -> None:
        installed = [
            app
            for app in django_settings.INSTALLED_APPS
            if app != "django.contrib.auth"
        ]
        with routed(project), override_settings(INSTALLED_APPS=installed):
            assert check_permissions_known() == []


def _guarded(action_name: str) -> list[tuple[object, str]]:
    return _warned(f"The guard of the form action {action_name!r}")


class TestPermissionsKnownInFormGuards:
    """`next.W127` names a form action whose guard asks for an ungranted permission."""

    @pytest.fixture(autouse=True)
    def _access_declared(self, isolated_permissions) -> None:
        isolated_permissions.add("blog.edit_post", no_params)

    def test_an_unknown_permission_in_a_form_meta_warns(self) -> None:
        class KnownGuardUnknownForm(Form):
            class Meta:
                permission_required = ("auth.add_user", "blog.publish_post")

        assert _guarded("known_guard_unknown_form") == [
            (django_settings, "blog.publish_post")
        ]

    def test_an_unknown_permission_in_an_action_kwarg_warns(self) -> None:
        @action("known_guard_handler", permission_required="blog.publish_post")
        def known_guard_handler(request):
            return None

        assert _guarded("known_guard_handler") == [
            (django_settings, "blog.publish_post")
        ]

    @pytest.mark.parametrize(
        "perm",
        [
            pytest.param("auth.add_user", id="model-permission"),
            pytest.param("blog.publish_post", id="registered"),
        ],
    )
    def test_a_permission_something_grants_is_silent(self, perm: str) -> None:
        add_perm("blog.publish_post", no_params)

        class KnownGuardGrantedForm(Form):
            class Meta:
                permission_required = perm

        assert _guarded("known_guard_granted_form") == []

    def test_a_login_only_guard_is_silent(self) -> None:
        class KnownGuardLoginForm(Form):
            class Meta:
                login_required = True

        assert _guarded("known_guard_login_form") == []

    def test_silent_without_the_auth_app(self, settings) -> None:
        class KnownGuardNoAuthForm(Form):
            class Meta:
                permission_required = "blog.publish_post"

        assert _guarded("known_guard_no_auth_form") == [
            (django_settings, "blog.publish_post")
        ]
        settings.INSTALLED_APPS = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.auth"
        ]
        assert check_permissions_known() == []


@pytest.mark.usefixtures("isolated_declarations")
class TestPermissionsKnownBeforeAccess:
    """A form guard names any permission while the project declares no access."""

    def test_an_unknown_guard_permission_is_silent(self) -> None:
        class SilentGuardUnknownForm(Form):
            class Meta:
                permission_required = "blog.publish_post"

        assert _guarded("silent_guard_unknown_form") == []
