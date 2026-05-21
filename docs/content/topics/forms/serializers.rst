.. _topics-forms-serializers:

Frozen Form Specs
=================

The forms subsystem ships frozen dataclass descriptors that describe a form, a formset, or a single field as immutable, comparable descriptors.
This module (``next.forms.serializers``) is unrelated to JSON serializers for :doc:`the browser context object </content/topics/static-assets/js-context>`. Those live under ``next.static``.

Each descriptor is a frozen dataclass, so it is immutable and supports value equality.
This makes a descriptor safe to cache or compare across renders.

.. contents::
   :local:
   :depth: 2

Overview
--------

The module ``next.forms.serializers`` exposes five dataclasses.

FieldSpec.
   Render-time descriptor for one ``BoundField``.
   Includes the field kind, the input type, the current value, the selected values for choice fields, and an ``is_extra`` flag.

FormsetRowSpec.
   One row inside a formset spec.
   Includes the row fields, the hidden HTML, the delete field, the row errors, and an ``is_extra`` flag.

FormsetSpec.
   Template-friendly view of a Django formset.
   Includes the prefix, the model verbose name plural, the management form, every row, the non form errors, and a ``can_delete`` flag.

FormSectionSpec.
   One labelled section in a ``FormSpec``, matching a Django admin fieldset.
   Includes the section label, a description string (empty when none was supplied), and the tuple of ``FieldSpec`` it groups.

FormSpec.
   Top-level descriptor for rendering a form with optional sections.
   Includes a tuple of ``FormSectionSpec`` plus non field errors.

Use these specs when you need to inspect a form structure from Python or to render in a custom template engine.

Building a Spec
----------------

The module provides three constructor helpers.

.. code-block:: python
   :caption: building specs

   from next.forms import field_spec, formset_spec, form_spec

   spec_one_field = field_spec(form["title"])
   spec_one_formset = formset_spec(my_formset)
   spec_one_form = form_spec(form, [
       ("Basic info", {"fields": ["title", "body"]}),
       ("Meta", {"fields": ["tags", "is_public"], "description": "Optional"}),
   ])

The second argument of ``form_spec`` is a Django admin style ``fieldsets`` sequence of ``(label, options)`` pairs.
Each ``options`` mapping carries a ``fields`` list and an optional ``description``.
Each helper returns a frozen instance ready to pass into a template.

``field_spec`` accepts an ``is_extra`` keyword argument that defaults to ``False``.
It sets the ``is_extra`` field on the resulting ``FieldSpec``, a structural flag that marks a field as belonging to an extra formset row rather than an instance-backed row.
The flag describes the row's origin, not whether the user filled it in.
``formset_spec`` computes ``is_extra`` per row and propagates it to every ``FieldSpec`` and ``FormsetRowSpec`` it builds, so a template can tell an extra row apart from an instance-backed one and style or hide it accordingly.
A standalone ``field_spec`` call leaves ``is_extra`` at ``False`` unless the caller passes the argument.

Using a Spec in Templates
-------------------------

Specs are designed to render in any template engine because they expose stable, immutable attributes.
Some members are still Django bound objects, such as ``FieldSpec.bound`` and ``FormsetSpec.management_form``.
Use them when the standard ``{% form %}`` tag does not match the layout you want.

.. code-block:: jinja
   :caption: custom render

   {% for section in form.sections %}
     <fieldset>
       <legend>{{ section.label }}</legend>
       {% for field in section.fields %}
         {% if field.kind == "textarea" %}
           <textarea name="{{ field.bound.name }}">{{ field.value }}</textarea>
         {% elif field.kind == "checkbox" %}
           <input type="checkbox" name="{{ field.bound.name }}" {% if field.value %}checked{% endif %}>
         {% else %}
           <input type="{{ field.input_type }}" name="{{ field.bound.name }}" value="{{ field.value }}">
         {% endif %}
       {% endfor %}
     </fieldset>
   {% endfor %}

A custom renderer can read the same fields from Python.
The dataclass exposes a fixed set of attributes.

Field Kinds
-----------

Each field carries a ``FieldKind`` literal that classifies the widget.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - kind
     - Source widget
   * - ``textarea``
     - ``Textarea`` widget.
   * - ``checkbox``
     - ``CheckboxInput`` widget.
   * - ``select``
     - ``Select`` widget.
   * - ``select_multi``
     - ``SelectMultiple`` widget.
   * - ``input``
     - Every other widget. ``input_type`` carries the HTML input type.

Admin ``RelatedFieldWidgetWrapper`` widgets are unwrapped to their inner widget before classification.

The framework classifies the widget once when constructing the spec.
Custom renderers can branch on ``kind`` without re instantiating the widget.

Shared Dependency Cache Across Re-render
----------------------------------------

The dispatcher reuses one dependency cache across the initial bind and the re-render, so providers run once and templates rebuild cheaply.
See :ref:`topics-forms-validation-rerender` for the mechanics and the ``get_request_dep_cache`` access path.

Spec vs Bound Form
------------------

Specs are descriptors, not replacements.
A handler still works with a normal Django bound form and calls ``form.is_valid()`` and ``form.save()``.
A template can choose either path.

- The default ``{% form %}`` tag renders the bound form directly.
- A custom template engine or a server-rendered design system uses the spec for layout.

Pick the spec when the rendering engine cannot consume Django bound fields directly.

Common Patterns
---------------

Render a Form in a Different Engine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use the spec to ship form structure to a Jinja2 macro or a JSON consumer.
Each spec is a frozen dataclass, so a custom renderer can read its fields directly or build its own plain-dict projection.
A bare Django ``Form`` is not JSON-encodable, so a form destined for ``@context(..., serialize=True)`` must travel as a ``FormSpec`` (or a custom projection) rather than as the form instance itself.

Snapshot Diffing
~~~~~~~~~~~~~~~~

The dataclass ``__eq__`` does not detect structural drift on its own because ``FieldSpec.bound`` carries a Django ``BoundField`` without value equality.
Compare on stable attributes instead.

.. code-block:: python
   :caption: detect added or removed fields

   def field_names(spec: FormSpec) -> tuple[str, ...]:
       return tuple(field.bound.name for section in spec.sections for field in section.fields)

   added = set(field_names(new_spec)) - set(field_names(old_spec))
   removed = set(field_names(old_spec)) - set(field_names(new_spec))

System Integration
~~~~~~~~~~~~~~~~~~

Use ``form_spec`` to render a form inside another rendering layer such as the Django admin while keeping dispatch on next.dj.

See Also
--------

.. seealso::

   :doc:`validation-rerender` for how the dispatcher reuses the dependency cache.
   :doc:`/content/ref/forms` for the public ``next.forms.serializers`` API.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
