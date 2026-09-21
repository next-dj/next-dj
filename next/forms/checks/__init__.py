"""System checks for the forms subsystem.

Importing the package registers every check, and each submodule names the ids it owns.
"""

from .actions import (
    check_action_applied_to_class,
    check_action_guard_permissions,
    check_form_action_collisions,
    check_forms_outside_base_dir,
    check_instance_from_url_on_non_model_form,
    check_instance_from_url_unknown_field,
    check_invalid_form_meta_scope,
    check_shared_action_name_collisions,
    check_success_message_framework,
)
from .config import (
    check_form_action_backends_configuration,
    check_form_anchor_files,
    check_form_wizard_backend,
)
from .widgets import (
    check_component_widget_components,
    check_component_widget_field_types,
)
from .wizards import (
    check_form_wizard_sessions,
    check_form_wizard_steps,
    check_wizard_step_actions,
    check_wizard_step_field_collisions,
    check_wizard_step_file_fields,
    check_wizard_url_param_route,
)


__all__ = [
    "check_action_applied_to_class",
    "check_action_guard_permissions",
    "check_component_widget_components",
    "check_component_widget_field_types",
    "check_form_action_backends_configuration",
    "check_form_action_collisions",
    "check_form_anchor_files",
    "check_form_wizard_backend",
    "check_form_wizard_sessions",
    "check_form_wizard_steps",
    "check_forms_outside_base_dir",
    "check_instance_from_url_on_non_model_form",
    "check_instance_from_url_unknown_field",
    "check_invalid_form_meta_scope",
    "check_shared_action_name_collisions",
    "check_success_message_framework",
    "check_wizard_step_actions",
    "check_wizard_step_field_collisions",
    "check_wizard_step_file_fields",
    "check_wizard_url_param_route",
]
