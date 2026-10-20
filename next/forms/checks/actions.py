"""System checks for the form classes and `@action` handlers a project registers."""

from pathlib import Path

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.forms.dispatch.permissions import COMPONENT_MODULE, owner_rule
from next.pages.checks.access import access_in_use, load_declarations, tree_pages
from next.pages.checks.branches import EmptyBranch, empty_branches
from next.pages.checks.modules import declares_without_page, load_page
from next.pages.loaders import PAGE_MODULE
from next.permissions.access import auth_installed, declared, login_by_default
from next.ports import action_owners_slot
from next.utils import middleware_listed

from .sources import diagnostics, iter_registered_actions


_MESSAGE_MIDDLEWARE = "django.contrib.messages.middleware.MessageMiddleware"


@register(NEXT)
def check_form_action_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Flag two `@action` calls that share a name but come from different handlers."""
    return [
        Error(
            f"Form action {name!r} is registered by {len(fps)} different "
            "handlers. Rename one of them or move one to a different scope "
            "to avoid the collision.",
            obj=settings,
            id="next.E041",
        )
        for name, fps in diagnostics().action_collisions.items()
    ]


@register(NEXT)
def check_shared_action_name_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Error when one shared action name is declared by two different modules."""
    return [
        Error(
            f"Shared form action {name!r} is declared in "
            f"{len(scope_keys)} modules: "
            f"{', '.join(repr(key) for key in sorted(scope_keys))}. "
            "Lookups by bare name resolve to whichever module imported "
            "first. Rename one class or set Meta.scope = 'page' on one "
            "of them.",
            obj=settings,
            id="next.E046",
        )
        for name, scope_keys in diagnostics().shared_name_collisions.items()
    ]


@register(NEXT)
def check_forms_outside_base_dir(*args, **kwargs) -> list[CheckMessage]:
    """Warn when form classes are declared outside BASE_DIR."""
    return [
        DjangoWarning(
            f"Form class {cls_name!r} declared in {file_path!r} which is outside "
            "BASE_DIR. It won't be registered automatically.",
            id="next.W046",
        )
        for cls_name, file_path in diagnostics().outside_base_dir
    ]


@register(NEXT)
def check_invalid_form_meta_scope(*args, **kwargs) -> list[CheckMessage]:
    """Error when a form class Meta.scope or an @action scope is invalid."""
    class_errors: list[CheckMessage] = [
        Error(
            f"Form class {cls_name!r} has Meta.scope = {bad_value!r}. "
            "Valid values are 'page' and 'shared'.",
            id="next.E047",
        )
        for cls_name, bad_value in diagnostics().invalid_meta_scope
    ]
    action_errors: list[CheckMessage] = [
        Error(
            f"Action {qualname!r} declares scope={bad_value!r}. "
            "Valid values are 'page' and 'shared'.",
            id="next.E085",
        )
        for qualname, bad_value in diagnostics().invalid_action_scope
    ]
    return class_errors + action_errors


@register(NEXT)
def check_action_applied_to_class(*args, **kwargs) -> list[CheckMessage]:
    """Error when @action decorator was applied to a class."""
    return [
        Error(
            f"@action was applied to class {cls_name!r}. "
            "Form classes register automatically through __init_subclass__.",
            id="next.E053",
        )
        for cls_name in diagnostics().action_applied_to_class
    ]


@register(NEXT)
def check_instance_from_url_unknown_field(*args, **kwargs) -> list[CheckMessage]:
    """Error when Meta.instance_from_url references a field absent on the model."""
    return [
        Error(
            f"Form class {cls_name!r} sets Meta.instance_from_url referencing "
            f"{field!r}, which is not a field on {model_label}.",
            id="next.E048",
        )
        for (
            cls_name,
            model_label,
            field,
        ) in diagnostics().instance_from_url_unknown_field
    ]


@register(NEXT)
def check_instance_from_url_on_non_model_form(*args, **kwargs) -> list[CheckMessage]:
    """Error when Meta.instance_from_url is set on a class that is not a ModelForm."""
    return [
        Error(
            f"Form class {cls_name!r} sets Meta.instance_from_url but is not a "
            "ModelForm. Subclass next.forms.ModelForm to load instances by URL.",
            id="next.E049",
        )
        for cls_name in diagnostics().instance_from_url_on_non_model_form
    ]


@register(NEXT)
def check_action_guard_permissions(*args, **kwargs) -> list[CheckMessage]:
    """Warn when permission_required is declared without django.contrib.auth.

    Only the static `ActionGuard` is inspected, the per-request hooks run user code.
    """
    if auth_installed():
        return []
    return [
        DjangoWarning(
            f"Form action {meta.get('name')!r} declares permission_required "
            "but django.contrib.auth is not in INSTALLED_APPS, so the "
            "permission check cannot resolve users or permissions.",
            obj=settings,
            id="next.W060",
        )
        for meta in iter_registered_actions()
        if (guard := meta.get("guard")) is not None and guard.permissions
    ]


def _open_actions() -> dict[Path, list[str]]:
    """Return the actions of each page tree file that nothing guards, by file.

    An action declared outside a tree answers to the rule of its origin page alone.
    """
    found: dict[Path, list[str]] = {}
    for meta in iter_registered_actions():
        if meta.get("guard") is not None:
            continue
        source = Path(meta["file_path"])
        owner = owner_rule(source)
        if owner is None or owner.broken is not None:
            continue
        if owner.rule.requirements or owner.rule.public:
            continue
        found.setdefault(source, []).append(meta["name"])
    return found


@register(NEXT)
def check_public_actions(*args, **kwargs) -> list[CheckMessage]:
    """Warn about an action of a page tree that nothing guards (`next.W129`).

    It waits for the project to declare access, and the default login or a guard counts.
    """
    if login_by_default() or not access_in_use():
        return []
    return [
        DjangoWarning(
            f"{source} declares the actions {', '.join(map(repr, names))}, and no "
            "declaration of its page or of a page.py above it, no Meta guard and "
            "no default login guard them, so anyone may post to them.",
            hint=(
                "Declare page.login_required() or permission_required(), with "
                "inherit=True in a page.py above to guard a whole directory, set "
                "login_required on the action Meta, turn on "
                'NEXT_FRAMEWORK["LOGIN_REQUIRED"], or declare page.public() if the '
                "actions are meant for everyone."
            ),
            obj=str(source),
            id="next.W129",
        )
        for source, names in _open_actions().items()
    ]


@register(NEXT)
def check_declarations_without_page(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `page.py` with no body whose declaration binds nothing (`next.E166`).

    An action beside the file answers to its rule, so a directory with one passes.
    """
    owners = action_owners_slot.get().page_files()
    return [
        Error(
            f"{file_path} has no body, so it serves no page, no action of its "
            "directory answers to it, and it declares access without inherit=True, "
            "which only its own page would. The declaration protects nothing.",
            hint=(
                "Add inherit=True to guard the pages below it, or give the file a "
                "render function, a template attribute, or a template.djx."
            ),
            obj=str(file_path),
            id="next.E166",
        )
        for file_path in sorted(entry.page_path for entry in tree_pages())
        if file_path not in owners
        and declares_without_page(file_path, load_page(file_path))
    ]


def _vain_reach(branch: EmptyBranch) -> str:
    """Return what reaches no page in an empty branch, for the `next.W128` text.

    A file pairing `public()` with a requirement passes no `public()` to its subtree.
    """
    source = branch.source
    if source is None:
        return "it routes nothing"
    if source.name != PAGE_MODULE:
        return f"{source} wraps no page"
    if branch.access.undeclared:
        return f"the inherit_context=True context of {source} reaches no page"
    if branch.context:
        return (
            f"the inherit=True access and the inherit_context=True context of {source} "
            "reach no page"
        )
    effect = "opens" if branch.access.public else "guards"
    return f"the inherit=True access of {source} {effect} no page"


@register(NEXT)
def check_empty_branches(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a directory of a page tree with no page and no action (`next.W128`).

    One warning names the outermost such directory, since one fix there fills it all.
    """
    action_dirs = {owner.parent for owner in action_owners_slot.get().page_files()}
    return [
        DjangoWarning(
            f"{branch.directory} holds no page and no action, so "
            f"{_vain_reach(branch)}.",
            hint=(
                "Add a page.py with a render function or a template attribute, or a "
                "template.djx, in this directory or below it. Otherwise delete the "
                "directory, or move what it holds over the pages it is meant for."
            ),
            obj=str(branch.directory),
            id="next.W128",
        )
        for branch in empty_branches(action_dirs)
    ]


def _context_objects(source: Path) -> tuple[str, ...]:
    """Return the context names the permissions of a `component.py` are checked on."""
    if source.name != COMPONENT_MODULE:
        return ()
    found = (item.obj for item in declared(source).requirements)
    return tuple(dict.fromkeys(obj for obj in found if isinstance(obj, str)))


@register(NEXT)
def check_component_action_objects(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `component.py` action whose permission names a context (`next.E165`).

    A POST renders no component, so the context is never there and the action refuses.
    """
    load_declarations()
    found: dict[Path, tuple[tuple[str, ...], list[str]]] = {}
    for meta in iter_registered_actions():
        source = Path(meta["file_path"])
        if objects := _context_objects(source):
            found.setdefault(source, (objects, []))[1].append(meta["name"])
    return [
        Error(
            f"{source} declares permission_required(obj=) on the context "
            f"{', '.join(map(repr, objects))} and the actions "
            f"{', '.join(map(repr, names))}. An action POST renders no component, "
            "so the context is never there and the actions refuse every user.",
            hint=(
                "Check the object in the form the action binds, through "
                "has_object_permission() or a Meta permission_required on a permission "
                "add_perm() registers, or move the actions to the page.py of the page."
            ),
            obj=str(source),
            id="next.E165",
        )
        for source, (objects, names) in found.items()
    ]


def _declares_success_message(target: object) -> bool:
    """Return True when the class declares a non-empty Meta.success_message."""
    return bool(getattr(getattr(target, "Meta", None), "success_message", ""))


@register(NEXT)
def check_success_message_framework(*args, **kwargs) -> list[CheckMessage]:
    """Warn when Meta.success_message is declared without the messages framework."""
    has_app = "django.contrib.messages" in settings.INSTALLED_APPS
    has_middleware = middleware_listed(settings.MIDDLEWARE or (), _MESSAGE_MIDDLEWARE)
    if has_app and has_middleware:
        return []
    return [
        DjangoWarning(
            f"Form action {meta.get('name')!r} declares Meta.success_message "
            "but the messages framework is not fully installed. Add "
            "django.contrib.messages to INSTALLED_APPS and MessageMiddleware "
            "to MIDDLEWARE, or the submission will raise MessageFailure.",
            obj=settings,
            id="next.W061",
        )
        for meta in iter_registered_actions()
        if _declares_success_message(meta.get("form_class"))
        or _declares_success_message(meta.get("wizard_class"))
    ]


__all__ = [
    "check_action_applied_to_class",
    "check_action_guard_permissions",
    "check_component_action_objects",
    "check_declarations_without_page",
    "check_empty_branches",
    "check_form_action_collisions",
    "check_forms_outside_base_dir",
    "check_instance_from_url_on_non_model_form",
    "check_instance_from_url_unknown_field",
    "check_invalid_form_meta_scope",
    "check_public_actions",
    "check_shared_action_name_collisions",
    "check_success_message_framework",
]
