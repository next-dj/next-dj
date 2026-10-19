import inspect
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Annotated, Literal, get_args, get_origin

import pytest
from django.contrib.auth.models import AbstractUser, Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.http import Http404, HttpRequest
from django.test import Client

from next.deps import (
    DependencyResolver,
    Depends,
    DObject,
    UnboundObjectError,
    UnknownDependencyError,
    objects,
)
from next.deps.introspect import cached_type_hints
from next.deps.objects import (
    SCOPE_DEPENDENCY,
    ObjectProvider,
    UnservedObjectProvider,
    object_target,
    scoped_row,
    url_row,
)
from next.pages.context import ContextByNameProvider
from next.pages.loaders import _load_python_module
from next.testing import make_resolution_context
from next.urls import UrlKwargsProvider
from next.urls.signals import router_reloaded
from tests.support import (
    bound_dependency,
    inspect_parameter,
    restored_resolver,
    routed,
    typing_optional,
    write_page,
)


PARENT = "permissions"

UNRESOLVED_MODEL_SOURCE = """
from __future__ import annotations

from typing import TYPE_CHECKING

from next.deps import DObject

if TYPE_CHECKING:
    from django.contrib.auth.models import Group


def view(group: DObject[Group]) -> Group:
    return group
"""

UNRESOLVED_REQUEST_SOURCE = """
from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth.models import Group

from next.deps import DObject

if TYPE_CHECKING:
    from django.http import HttpRequest


def view(request: HttpRequest, group: DObject[Group]) -> Group:
    return group
"""
LINEAR_RESOLVER = "next.deps.linear.LinearDependencyResolver"
NO_GROUP = "No Group matches the given query."
MISSING_PK = 10**6

CONTENT_TYPE_PAGE = """
from django.contrib.contenttypes.models import ContentType

from next.deps import DObject
from next.pages import context


@context("content_type", inherit_context=True)
def content_type(content_type: DObject[ContentType]) -> ContentType:
    return content_type


template = "{{ content_type.model }}"
"""

PERMISSION_PAGE = """
from django.contrib.auth.models import Permission

from next.deps import DObject
from next.pages import context


@context("permissions", inherit_context=True)
def permissions(permission: DObject[Permission, "content_type"]) -> Permission:
    return permission


template = "{{ content_type.model }}/{{ permissions.codename }}"
"""

GROUP_PAGE = """
from django.contrib.auth.models import Group

from next.deps import DObject
from next.pages import context


@context("group")
def group(group: DObject[Group, "permissions"]) -> Group:
    return group


template = "{{ content_type.model }}/{{ permissions.codename }}/{{ group.name }}"
"""

TWICE_PAGE = """
from django.contrib.auth.models import Group

from next.deps import DObject
from next.pages import context


@context("title")
def title(group: DObject[Group]) -> str:
    return group.name


@context("shout")
def shout(group: DObject[Group]) -> str:
    return group.name.upper()


template = "{{ title }}|{{ shout }}"
"""


def _group(group: DObject[Group]) -> Group:
    return group


def _member(group: DObject[Group, PARENT]) -> Group:
    return group


def _pair(group: DObject[Group], other: DObject[Group]) -> tuple[Group, Group]:
    return group, other


def _mixed(
    content_type: DObject[ContentType], group: DObject[Group]
) -> tuple[ContentType, Group]:
    return content_type, group


def _defaulted(group: DObject[Group] = Depends("raw")) -> Group:
    return group


def _bare(group: DObject) -> Group:
    return group


def _optional(group: DObject[Group] | None) -> Group | None:
    return group


def _permission(permission: DObject[Permission]) -> Permission:
    return permission


class _CountingScope:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self.value


def _chain_tree(root: Path) -> Path:
    write_page(root, "[int:content_type]", CONTENT_TYPE_PAGE)
    write_page(root, "[int:content_type]/[int:permission]", PERMISSION_PAGE)
    write_page(root, "[int:content_type]/[int:permission]/[int:group]", GROUP_PAGE)
    return root


@pytest.fixture()
def tenant_scope() -> _CountingScope:
    return _CountingScope("auth")


@pytest.fixture()
def tenant_resolver(
    resolver_class: type[DependencyResolver], scoped_models, tenant_scope
) -> DependencyResolver:
    fresh = resolver_class()
    fresh.dependency("scope")(tenant_scope)
    return fresh


@pytest.fixture()
def editors() -> Group:
    group = Group.objects.create(name="auth:editors")
    group.permissions.add(Permission.objects.get(codename="change_group"))
    return group


@pytest.fixture()
def outsiders() -> Group:
    return Group.objects.create(name="other:outsiders")


@pytest.fixture(params=[None, LINEAR_RESOLVER], ids=["compiled", "linear"])
def render_framework(request) -> Generator[dict[str, str], None, None]:
    with restored_resolver():
        yield {} if request.param is None else {"DEPENDENCY_RESOLVER": request.param}


class TestMarker:
    """`DObject[...]` builds an alias the resolver reads, or refuses its shape."""

    def test_a_model_alone_is_the_only_argument(self) -> None:
        marker = DObject[Group]
        assert get_origin(marker) is DObject
        assert get_args(marker) == (Group,)

    def test_a_parent_travels_as_a_literal(self) -> None:
        assert get_args(DObject[Group, "permissions"]) == (
            Group,
            Literal["permissions"],
        )

    def test_a_wrapped_parent_builds_the_same_alias(self) -> None:
        assert DObject[Group, Literal["permissions"]] == DObject[Group, "permissions"]

    def test_the_resolved_hint_keeps_model_and_parent(self) -> None:
        hint = cached_type_hints(_member)["group"]
        assert object_target(hint) == (Group, "permissions")

    @pytest.mark.parametrize(
        ("item", "fragment"),
        [
            ((), "got 0 arguments"),
            ((Group, "permissions", "extra"), "got 3 arguments"),
            (int, "takes a model class first, got <class 'int'>"),
            ("Group", "takes a model class first, got 'Group'"),
            (AbstractUser, "AbstractUser is abstract"),
            ((Group, "not a name"), "a field of Group, got 'not a name'"),
            ((Group, 3), "a field of Group, got 3"),
        ],
        ids=[
            "no_argument",
            "three_arguments",
            "plain_class",
            "model_name",
            "abstract_model",
            "parent_not_identifier",
            "parent_not_string",
        ],
    )
    def test_an_unservable_shape_is_refused(self, item: object, fragment: str) -> None:
        with pytest.raises(TypeError, match=fragment):
            DObject[item]


class TestObjectTarget:
    """`object_target` reads the model and parent off a `DObject` annotation."""

    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [
            (DObject[Group], (Group, None)),
            (DObject[Group, "permissions"], (Group, "permissions")),
            (Annotated[DObject[Group], "doc"], (Group, None)),
            (DObject, None),
            (Group, None),
            (list[Group], None),
            (int, None),
        ],
        ids=["model", "parent", "annotated", "bare", "model_class", "list", "int"],
    )
    def test_the_target_of_an_annotation(self, annotation, expected) -> None:
        assert object_target(annotation) == expected


class TestStaticClaim:
    """The provider claims every annotation naming `DObject`, and nothing else."""

    @pytest.mark.parametrize(
        ("annotation", "claimed"),
        [
            (DObject, True),
            (DObject[Group], True),
            (DObject[Group, "permissions"], True),
            (Annotated[DObject[Group], "doc"], True),
            (Annotated[DObject, "doc"], True),
            (DObject[Group] | None, True),
            (DObject | None, True),
            (typing_optional(DObject[Group]), True),
            (int, False),
            (Group, False),
            (list[Group], False),
            (Group | None, False),
            (Annotated[Group, "doc"], False),
            ("DObject[Group]", True),
            ("deps.DObject[Group] | None", True),
            ("MyDObjectish[Group]", False),
            ("_DObject[Group]", False),
            ("Group", False),
            (inspect.Parameter.empty, False),
        ],
        ids=[
            "bare",
            "model",
            "parent",
            "annotated",
            "annotated_bare",
            "pep604_union",
            "bare_union",
            "typing_optional",
            "int",
            "model_class",
            "list",
            "model_union",
            "annotated_model",
            "unresolved_string",
            "unresolved_dotted_union",
            "longer_name",
            "underscored_name",
            "unresolved_model",
            "unannotated",
        ],
    )
    def test_the_verdict_holds_in_every_context(self, annotation, claimed) -> None:
        provider = ObjectProvider(DependencyResolver())
        param = inspect_parameter("group", annotation)
        assert provider.static_can_handle(param) is claimed
        assert provider.can_handle(param, make_resolution_context()) is claimed

    def test_it_runs_ahead_of_every_shipped_provider(self) -> None:
        providers = DependencyResolver()
        providers._sync_providers()
        assert type(providers._providers[0]) is ObjectProvider


class TestCompileRefusals:
    """A claimed annotation no lookup could serve fails loudly, never fills raw."""

    @pytest.mark.parametrize(
        ("func", "fragment"),
        [
            (_bare, "annotated with a bare DObject"),
            (_optional, "puts DObject in a union"),
        ],
        ids=["bare", "union"],
    )
    def test_an_unservable_annotation_raises(
        self, resolver_class, scoped_models, func, fragment: str
    ) -> None:
        with pytest.raises(TypeError, match=fragment):
            resolver_class().resolve_dependencies(func, group=1)

    @pytest.mark.parametrize(
        "source",
        [UNRESOLVED_MODEL_SOURCE, UNRESOLVED_REQUEST_SOURCE],
        ids=["model_unresolved", "other_hint_unresolved"],
    )
    @pytest.mark.django_db()
    def test_an_unresolved_hint_raises_on_every_resolve(
        self, resolver_class, scoped_models, outsiders, tmp_path: Path, source: str
    ) -> None:
        module_file = tmp_path / "unresolved.py"
        module_file.write_text(source)
        module = _load_python_module(module_file)
        assert module is not None
        fresh = resolver_class()
        fresh.dependency(SCOPE_DEPENDENCY)(lambda: "auth")
        for _attempt in range(2):
            with pytest.raises(
                TypeError, match='Parameter "group" keeps the annotation'
            ):
                fresh.resolve_dependencies(module.view, group=str(outsiders.pk))

    @pytest.mark.parametrize("hook", [None, "rows"], ids=["missing", "not_callable"])
    def test_a_manager_without_visible_to_raises(
        self, resolver_class, monkeypatch: pytest.MonkeyPatch, hook: object
    ) -> None:
        if hook is not None:
            monkeypatch.setattr(
                Permission._default_manager, "visible_to", hook, raising=False
            )
        with pytest.raises(
            TypeError, match=r"auth\.Permission\._default_manager\.visible_to\(scope\)"
        ):
            resolver_class().resolve_dependencies(_permission, permission=1)


@pytest.mark.usefixtures("scoped_models")
class TestRowsMemo:
    """The `visible_to` hook of a model is read once a model until a router reload."""

    @pytest.fixture()
    def reads(self, monkeypatch: pytest.MonkeyPatch) -> list[type[Model]]:
        seen: list[type[Model]] = []
        read = objects._scoped_query

        def spy(model: type[Model]) -> object:
            seen.append(model)
            return read(model)

        monkeypatch.setattr(objects, "_scoped_query", spy)
        return seen

    @pytest.mark.parametrize(
        "check",
        [
            pytest.param(lambda: url_row(Group, None, {"group": "x"}, {}), id="url"),
            pytest.param(
                lambda: scoped_row(Group, {"pk": "x"}, HttpRequest()), id="scoped"
            ),
        ],
    )
    def test_each_check_reuses_the_hook(self, reads, check) -> None:
        for _ in range(2):
            with pytest.raises(Http404):
                check()

        assert reads == [Group]

    def test_a_router_reload_reads_it_again(self, reads) -> None:
        held = objects._rows(Group)
        router_reloaded.send(sender=type(self))

        assert objects._rows(Group) is not held
        assert reads == [Group, Group]


@pytest.mark.django_db()
class TestLookup:
    """A `DObject` parameter receives the row its scope may see, or a 404."""

    def test_a_visible_row_fills_the_parameter(
        self, tenant_resolver, tenant_scope, editors
    ) -> None:
        assert tenant_resolver.resolve_dependencies(_group, group=str(editors.pk)) == {
            "group": editors
        }
        assert tenant_scope.calls == 1

    @pytest.mark.parametrize("row", ["hidden", "missing"])
    def test_an_unseen_row_answers_404(
        self, tenant_resolver, editors, outsiders, row: str
    ) -> None:
        pk = outsiders.pk if row == "hidden" else MISSING_PK
        with pytest.raises(Http404, match=NO_GROUP):
            tenant_resolver.resolve_dependencies(_group, group=pk)

    def test_a_malformed_pk_answers_404_before_the_scope(
        self, tenant_resolver, tenant_scope, django_assert_num_queries
    ) -> None:
        with django_assert_num_queries(0), pytest.raises(Http404, match=NO_GROUP):
            tenant_resolver.resolve_dependencies(_group, group="abc")
        assert tenant_scope.calls == 0

    def test_a_missing_kwarg_is_a_wiring_error(self, tenant_resolver) -> None:
        with pytest.raises(UnboundObjectError) as caught:
            tenant_resolver.resolve_dependencies(_group, slug="a", id=1)
        assert caught.value.param_name == "group"
        assert caught.value.key == "group"
        assert caught.value.parent is False
        assert "(captured: id, slug)" in str(caught.value)

    def test_a_component_never_reads_the_template_value(
        self, tenant_resolver, editors
    ) -> None:
        with pytest.raises(UnboundObjectError, match=r"\(captured: none\)"):
            tenant_resolver.resolve_with_template_context(
                _group, template_context={"group": editors}
            )

    def test_an_unregistered_scope_names_the_dependency(
        self, resolver_class, scoped_models, editors
    ) -> None:
        with pytest.raises(UnknownDependencyError) as caught:
            resolver_class().resolve_dependencies(_group, group=editors.pk)
        assert caught.value.name == SCOPE_DEPENDENCY
        assert caught.value.param_name == "group"
        assert caught.value.func is _group

    def test_the_scope_value_reaches_visible_to(
        self, resolver_class, scoped_models, editors
    ) -> None:
        fresh = resolver_class()
        fresh.dependency(SCOPE_DEPENDENCY)(lambda: "other")
        with pytest.raises(Http404):
            fresh.resolve_dependencies(_group, group=editors.pk)

    def test_a_row_a_join_repeats_is_found_with_one_query(
        self, resolver_class, scoped_models, monkeypatch, django_assert_num_queries
    ) -> None:
        group = Group.objects.create(name="auth:joined")
        group.permissions.add(*Permission.objects.filter(codename__endswith="_group"))
        monkeypatch.setattr(
            Group._default_manager,
            "visible_to",
            lambda _scope: Group.objects.filter(
                permissions__codename__endswith="_group"
            ),
        )
        fresh = resolver_class()
        fresh.dependency(SCOPE_DEPENDENCY)(lambda: "auth")
        with django_assert_num_queries(1):
            assert fresh.resolve_dependencies(_group, group=group.pk) == {
                "group": group
            }


@pytest.mark.django_db()
class TestParent:
    """A parent `DObject` loads only a row that belongs to the parent in context."""

    @pytest.mark.parametrize(
        "owner",
        [
            lambda: Permission.objects.get(codename="change_group"),
            lambda: Permission.objects.get(codename="change_group").pk,
        ],
        ids=["row", "pk"],
    )
    def test_a_row_of_the_parent_fills_the_parameter(
        self, tenant_resolver, editors, owner: Callable[[], object]
    ) -> None:
        resolved = tenant_resolver.resolve_dependencies(
            _member, group=editors.pk, _context_data={"permissions": owner()}
        )
        assert resolved == {"group": editors}

    def test_a_row_of_another_parent_answers_404(
        self, tenant_resolver, editors
    ) -> None:
        foreign = Permission.objects.get(codename="delete_group")
        with pytest.raises(Http404, match=NO_GROUP):
            tenant_resolver.resolve_dependencies(
                _member, group=editors.pk, _context_data={"permissions": foreign}
            )

    def test_a_none_parent_answers_404_without_a_query(
        self, tenant_resolver, tenant_scope, editors, django_assert_num_queries
    ) -> None:
        with django_assert_num_queries(0), pytest.raises(Http404, match=NO_GROUP):
            tenant_resolver.resolve_dependencies(
                _member, group=editors.pk, _context_data={"permissions": None}
            )
        assert tenant_scope.calls == 0

    def test_a_missing_parent_is_a_wiring_error(self, tenant_resolver, editors) -> None:
        with pytest.raises(UnboundObjectError) as caught:
            tenant_resolver.resolve_dependencies(_member, group=editors.pk)
        assert caught.value.param_name == "group"
        assert caught.value.key == "permissions"
        assert caught.value.parent is True


@pytest.mark.django_db()
class TestRowCache:
    """One resolution loads each row once and keys it by model, pk and parent."""

    def test_two_parameters_on_one_row_share_one_query(
        self, tenant_resolver, tenant_scope, editors, django_assert_num_queries
    ) -> None:
        with django_assert_num_queries(1):
            resolved = tenant_resolver.resolve_dependencies(
                _pair, group=str(editors.pk), other=editors.pk
            )
        assert resolved["group"] is resolved["other"]
        assert tenant_scope.calls == 1

    def test_two_rows_take_two_queries(
        self, tenant_resolver, editors, django_assert_num_queries
    ) -> None:
        reviewers = Group.objects.create(name="auth:reviewers")
        with django_assert_num_queries(2):
            resolved = tenant_resolver.resolve_dependencies(
                _pair, group=editors.pk, other=reviewers.pk
            )
        assert resolved == {"group": editors, "other": reviewers}

    def test_one_pk_of_two_models_loads_both(self, tenant_resolver) -> None:
        content_type = ContentType.objects.get_for_model(Group)
        group = Group.objects.create(pk=content_type.pk, name="auth:twin")
        resolved = tenant_resolver.resolve_dependencies(
            _mixed, content_type=content_type.pk, group=content_type.pk
        )
        assert resolved == {"content_type": content_type, "group": group}

    def test_a_cached_row_is_not_served_under_another_parent(
        self, tenant_resolver, editors
    ) -> None:
        cache: dict[str, object] = {}
        allowed = Permission.objects.get(codename="change_group")
        foreign = Permission.objects.get(codename="delete_group")
        first = tenant_resolver.resolve_dependencies(
            _member,
            group=editors.pk,
            _cache=cache,
            _context_data={"permissions": allowed},
        )
        assert first == {"group": editors}
        with pytest.raises(Http404):
            tenant_resolver.resolve_dependencies(
                _member,
                group=editors.pk,
                _cache=cache,
                _context_data={"permissions": foreign},
            )


@pytest.mark.django_db()
class TestPriority:
    """Neither a `Depends` default nor a context value of the name skips the scope."""

    def test_a_hidden_row_stays_hidden(self, tenant_resolver, outsiders) -> None:
        tenant_resolver.dependency("raw")(lambda: outsiders)
        with pytest.raises(Http404, match=NO_GROUP):
            tenant_resolver.resolve_dependencies(
                _defaulted, group=outsiders.pk, _context_data={"group": outsiders}
            )

    def test_the_scoped_row_wins_over_both(
        self, tenant_resolver, editors, outsiders
    ) -> None:
        tenant_resolver.dependency("raw")(lambda: outsiders)
        resolved = tenant_resolver.resolve_dependencies(
            _defaulted, group=editors.pk, _context_data={"group": outsiders}
        )
        assert resolved == {"group": editors}


@pytest.mark.django_db()
class TestNestedRoutes:
    """Inherited page contexts chain `DObject` parents across two levels."""

    def test_every_level_renders_with_one_query_per_row(
        self,
        tmp_path: Path,
        scoped_models,
        tenant_scope,
        editors,
        render_framework,
        django_assert_num_queries,
    ) -> None:
        root = _chain_tree(tmp_path / "pages")
        content_type = ContentType.objects.get_for_model(Group)
        permission = Permission.objects.get(codename="change_group")
        url = f"/{content_type.pk}/{permission.pk}/{editors.pk}/"
        with (
            routed(root, **render_framework),
            bound_dependency(SCOPE_DEPENDENCY, tenant_scope),
            django_assert_num_queries(3),
        ):
            response = Client().get(url)
        assert response.status_code == 200
        assert response.content.decode() == "group/change_group/auth:editors"
        assert tenant_scope.calls == 1

    def test_the_middle_level_renders_its_own_chain(
        self, tmp_path: Path, scoped_models, editors
    ) -> None:
        root = _chain_tree(tmp_path / "pages")
        content_type = ContentType.objects.get_for_model(Group)
        permission = Permission.objects.get(codename="change_group")
        with routed(root), bound_dependency(SCOPE_DEPENDENCY, lambda: "auth"):
            response = Client().get(f"/{content_type.pk}/{permission.pk}/")
        assert response.status_code == 200
        assert response.content.decode() == "group/change_group"

    @pytest.mark.parametrize(
        ("model", "codename", "row"),
        [
            (Session, None, None),
            (Group, "change_user", None),
            (Group, "delete_group", "editors"),
            (Group, "change_group", "missing"),
        ],
        ids=[
            "foreign_content_type",
            "permission_of_another_content_type",
            "group_of_another_permission",
            "missing_group",
        ],
    )
    def test_a_row_outside_the_chain_answers_404(
        self,
        tmp_path: Path,
        scoped_models,
        editors,
        render_framework,
        model: type[Model],
        codename: str | None,
        row: str | None,
    ) -> None:
        root = _chain_tree(tmp_path / "pages")
        pks = [ContentType.objects.get_for_model(model).pk]
        if codename is not None:
            pks.append(Permission.objects.get(codename=codename).pk)
        if row is not None:
            pks.append(editors.pk if row == "editors" else MISSING_PK)
        with (
            routed(root, **render_framework),
            bound_dependency(SCOPE_DEPENDENCY, lambda: "auth"),
        ):
            response = Client().get("/" + "".join(f"{pk}/" for pk in pks))
        assert response.status_code == 404


@pytest.mark.django_db()
class TestScopeOverride:
    """The project `scope` dependency decides which rows a page may load."""

    @pytest.mark.parametrize(
        ("scope", "status"),
        [("member", 200), ("stranger", 404), ("acme", 200), ("other", 404)],
        ids=["member", "stranger", "own_tenant", "other_tenant"],
    )
    def test_one_pk_is_seen_by_one_scope_only(
        self, tmp_path: Path, scoped_models, scope: str, status: int
    ) -> None:
        staff = Group.objects.create(name="acme:staff")
        member = User.objects.create(username="member")
        member.groups.add(staff)
        stranger = User.objects.create(username="stranger")
        root = tmp_path / "pages"
        write_page(root, "[int:group]", TWICE_PAGE)
        value = {"member": member, "stranger": stranger}.get(scope, scope)
        with routed(root), bound_dependency(SCOPE_DEPENDENCY, lambda: value):
            response = Client().get(f"/{staff.pk}/")
        assert response.status_code == status

    @pytest.mark.parametrize(
        ("username", "status"),
        [("member", 200), ("stranger", 404)],
        ids=["member", "stranger"],
    )
    def test_the_scope_may_read_the_request(
        self, tmp_path: Path, scoped_models, username: str, status: int
    ) -> None:
        staff = Group.objects.create(name="acme:staff")
        User.objects.create(username="member").groups.add(staff)
        User.objects.create(username="stranger")
        root = tmp_path / "pages"
        write_page(root, "[int:group]", TWICE_PAGE)
        client = Client()
        client.force_login(User.objects.get(username=username))

        def scope(request: HttpRequest) -> object:
            return request.user

        with routed(root), bound_dependency(SCOPE_DEPENDENCY, scope):
            response = client.get(f"/{staff.pk}/")
        assert response.status_code == status

    def test_two_contexts_on_one_row_query_it_once(
        self,
        tmp_path: Path,
        scoped_models,
        tenant_scope,
        editors,
        render_framework,
        django_assert_num_queries,
    ) -> None:
        root = tmp_path / "pages"
        write_page(root, "[int:group]", TWICE_PAGE)
        with (
            routed(root, **render_framework),
            bound_dependency(SCOPE_DEPENDENCY, tenant_scope),
            django_assert_num_queries(1),
        ):
            response = Client().get(f"/{editors.pk}/")
        assert response.content.decode() == "auth:editors|AUTH:EDITORS"
        assert tenant_scope.calls == 1


_RAW = "1"


@pytest.mark.parametrize(
    "provider", [UrlKwargsProvider(), ContextByNameProvider()], ids=["url", "context"]
)
class TestNameProvidersRefuseObjects:
    """A same-named URL kwarg or context value never fills a `DObject` parameter."""

    @pytest.mark.parametrize(
        ("annotation", "claimed"),
        [
            pytest.param(DObject[Group], False, id="model"),
            pytest.param(DObject[Group, "permissions"], False, id="parent"),
            pytest.param(Annotated[DObject[Group], "doc"], False, id="annotated"),
            pytest.param(DObject[Group] | None, False, id="union"),
            pytest.param("DObject[Group]", False, id="unresolved"),
            pytest.param(int, True, id="plain"),
        ],
    )
    def test_the_claim(self, provider, annotation, claimed) -> None:
        param = inspect_parameter("group", annotation)
        context = make_resolution_context(
            url_kwargs={"group": _RAW}, context_data={"group": _RAW}
        )
        assert provider.static_can_handle(param) is (None if claimed else False)
        assert provider.can_handle(param, context) is claimed

    def test_an_explicit_resolver_leaves_the_parameter_unfilled(self, provider) -> None:
        resolved = DependencyResolver(provider).resolve_dependencies(
            _group, group=_RAW, _context_data={"group": _RAW}
        )
        assert resolved["group"] is None


def _without_object_provider(resolver: DependencyResolver) -> DependencyResolver:
    resolver._sync_providers()
    loader = next(p for p in resolver._providers if isinstance(p, ObjectProvider))
    resolver.remove_provider(loader)
    return resolver


class TestUnservedObject:
    """A `DObject` every provider passes over is refused, never filled raw."""

    def test_it_runs_after_every_shipped_provider(self) -> None:
        providers = DependencyResolver()
        providers._sync_providers()
        assert type(providers._providers[-1]) is UnservedObjectProvider

    @pytest.mark.parametrize(
        ("annotation", "claimed"), [(DObject[Group], True), (Group, False)]
    )
    def test_it_claims_what_the_object_provider_claims(
        self, annotation: object, *, claimed: bool
    ) -> None:
        provider = UnservedObjectProvider()
        param = inspect_parameter("group", annotation)
        assert provider.static_can_handle(param) is claimed
        assert provider.can_handle(param, make_resolution_context()) is claimed

    def test_a_resolver_without_the_object_provider_refuses_on_every_resolve(
        self, resolver_class
    ) -> None:
        fresh = _without_object_provider(resolver_class())
        for _attempt in range(2):
            with pytest.raises(
                ImproperlyConfigured,
                match='DObject parameter "group" reached no provider',
            ):
                fresh.resolve_dependencies(_group, group=_RAW)
