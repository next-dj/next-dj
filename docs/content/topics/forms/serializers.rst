.. _topics-forms-serializers:

Frozen Form Specs
=================

The forms subsystem ships frozen dataclass descriptors that describe a form, a formset, or a single field as plain Python data.
The specs survive across re-render because the framework caches them on the request and reuses them after a failed POST.
Use them when you need to inspect a form structure from Python or to render forms in a custom template engine.

.. contents::
   :local:
   :depth: 2

Overview
--------

The module ``next.forms.serializers`` exposes four dataclasses.

FieldSpec.
   Render-time descriptor for one ``BoundField``.
   Includes the field kind, the input type, the current value, and the selected values for choice fields.

FormsetRowSpec.
   One row inside a formset spec.
   Includes the row fields, the hidden HTML, the delete field, the row errors, and a flag for extra rows.

FormsetSpec.
   Template-friendly view of a Django formset.
   Includes the management form, every row, the non form errors, and a ``can_delete`` flag.

FormSpec.
   Top-level descriptor for rendering a form with optional sections.
   Includes a tuple of ``FormSectionSpec`` plus non field errors.

Every spec is a frozen ``@dataclass`` so it is hashable and safe to share across threads.

Building a Spec
---------------

The module provides three constructor helpers.

.. code-block:: python
   :caption: building specs

   from next.forms.serializers import (
       field_spec,
       formset_spec,
       form_spec,
   )

   spec_one_field = field_spec(form["title"])
   spec_one_formset = formset_spec(my_formset)
   spec_one_form = form_spec(form, sections=[
       ("Basic info", "", ["title", "body"]),
       ("Meta", "Optional", ["tags", "is_public"]),
   ])

Each helper returns a frozen instance ready to pass into a template.

Using a Spec in Templates
-------------------------

Specs are designed to render in any template engine because they expose plain Python attributes.
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
The dataclass shape is stable across releases.

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

The framework classifies the widget once when constructing the spec.
Custom renderers can branch on ``kind`` without re instantiating the widget.

Shared Dependency Cache Across Re-render
----------------------------------------

The dispatcher saves the dependency cache from the initial render of a form page.
On a failed POST the cache is reattached to the re-rendered page so context functions and providers do not run twice.
Two consequences flow from this.

Idempotent providers.
   Custom providers must not depend on a fresh evaluation between initial render and re-render.
   The cache holds the value from the first call.

Cheap re-render.
   Form pages re-render quickly because layouts, context functions, and components reuse cached values.

The framework exposes the cache through ``next.deps.cache.get_request_dep_cache`` when an action needs to read it.

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
The dataclass form is friendly to ``dataclasses.asdict`` for one direction conversions.

Snapshot Diffing
~~~~~~~~~~~~~~~~

Cache a ``FormSpec`` from one render and compare against the spec of the next render to detect added or removed fields.
The frozen dataclass implements ``__eq__`` automatically.

System Integration
~~~~~~~~~~~~~~~~~~

The Django admin example uses ``form_spec`` to render forms inside the admin chrome while keeping all dispatch behaviour inside next.dj.
See ``examples/admin`` for the complete walkthrough.

See Also
--------

.. seealso::

   :doc:`validation-rerender` for how the dispatcher reuses the dependency cache.
   :doc:`/content/ref/forms` for the public ``next.forms.serializers`` API.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
