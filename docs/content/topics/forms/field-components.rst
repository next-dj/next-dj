.. _topics-forms-field-components:

Field Components
================

Changing an input's classes or accessibility markup across a project usually means editing every form, or overriding Django's project-wide ``FORM_RENDERER`` and its widget templates.
``ComponentWidget`` keeps that change in one place.
It is a form widget that renders a registered next.dj component instead of a Django widget template.
One field maps to one component.
The component owns the markup and the styling, so the same ``input`` or ``textarea`` lives in a single ``component.djx`` that every form reuses, and a styling or accessibility change ships once instead of propagating across every form.

.. contents::
   :local:
   :depth: 2

Declaration
-----------

Import the widget and attach it to a field with the component name and any extra props.

.. code-block:: python
   :caption: a plain Form with field-level widgets

   import next.forms
   from next.forms import ComponentWidget

   class ArticleCreateForm(next.forms.Form):
       slug = next.forms.CharField(widget=ComponentWidget("input", placeholder="URL slug"))
       title = next.forms.CharField(widget=ComponentWidget("input", placeholder="Title"))
       body_md = next.forms.CharField(widget=ComponentWidget("textarea", rows=12))

A ``ModelForm`` declares the same widgets through ``Meta.widgets``, mapping each field name to a ``ComponentWidget`` value.

.. code-block:: python
   :caption: a ModelForm with Meta.widgets

   import next.forms
   from next.forms import ComponentWidget
   from wiki.models import Article

   class ArticleEditForm(next.forms.ModelForm):
       class Meta:
           model = Article
           fields = ["slug", "title", "body_md"]
           instance_from_url = "slug"
           widgets = {
               "slug": ComponentWidget("input", placeholder="URL slug"),
               "title": ComponentWidget("input", placeholder="Title"),
               "body_md": ComponentWidget("textarea", rows=12),
           }

Both forms render the field exactly like ``{{ form.slug }}`` in a ``{% form %}`` block.
The widget calls the component runtime in place of Django's stock widget template.

The constructor accepts a keyword-only ``attrs`` dict for persistent HTML attributes, alongside the component props.

.. code-block:: python
   :caption: persistent attrs versus component props

   slug = next.forms.CharField(
       widget=ComponentWidget("input", attrs={"data-role": "slug"}, placeholder="URL slug"),
   )

The ``attrs`` dict is merged Django-style through :meth:`~django.forms.Widget.build_attrs`, the same as on any Django widget, and render-time attributes win on a collision.
Every other keyword argument is a component prop spread to the top level of the component context.

The Context Contract
--------------------

When a field renders, the component template receives the values the bound field produced.

``name`` and ``value``.
   The field's HTML name and current value.
   ``value`` is the formatted display value, the result of the widget's :meth:`~django.forms.Widget.format_value`, and is ``None`` when the field is empty.
   These are authoritative and a component template should bind ``name`` and ``value`` to the rendered control.

``errors``.
   The bound field's errors as a list, empty on an unbound form.
   A component can render an error state straight from it without branching on whether the form is bound, and the shared ``input`` and ``textarea`` do exactly that.

HTML ``attrs``.
   Django builds the widget's HTML attributes such as ``id``, ``required``, and ``maxlength``.
   The widget spreads these to the top level, so a component reads ``{{ id }}`` or ``{{ required }}`` directly.
   The same dict is also available whole under the ``attrs`` key for a template that prefers to iterate it.

   Hyphenated attributes such as the accessibility hooks Django adds (``aria-invalid``, ``aria-describedby``) and any ``data-*`` cannot be read as template variables, because ``{{ aria-invalid }}`` is invalid template syntax.
   The widget exposes each one under an underscore alias at the top level, so a component reads ``{{ aria_invalid }}`` or ``{{ aria_describedby }}``.
   The raw mapping under ``attrs`` keeps the original hyphenated keys for iteration.

   .. code-block:: jinja
      :caption: reading an aliased attribute

      {% if aria_invalid %}aria-invalid="{{ aria_invalid }}"{% endif %}

Extra keyword arguments.
   Every keyword passed to ``ComponentWidget("input", placeholder=..., rows=...)`` is spread to the top level too.
   A ``placeholder`` argument reaches the template as ``{{ placeholder }}``.

.. warning::

   ``value`` is user-supplied input.
   On a bound form it carries what the visitor posted, so a component template must let Django auto-escape it.
   Rendering it through ``{{ value|safe }}`` or inside ``{% autoescape off %}`` turns the posted value into HTML, and the widget then wraps the whole render in a ``SafeString``, which produces a stored or reflected cross-site scripting vector.
   Bind ``value`` plainly, as ``value="{{ value }}"``.

   The ``attrs`` props and the extra keyword arguments are developer-supplied, not request input, so the same caution does not apply to them.

.. code-block:: jinja
   :caption: a minimal input component

   <input
     name="{{ name }}"
     {% if id %}id="{{ id }}"{% endif %}
     {% if value is not None %}value="{{ value }}"{% endif %}
     {% if placeholder %}placeholder="{{ placeholder }}"{% endif %}
     {% if required %}required{% endif %}
     class="flex h-10 w-full rounded-md border px-3 py-2 text-sm"
   />

Scope and Registration
----------------------

A ``ComponentWidget`` resolves its component the same way the ``{% component %}`` tag does, walking outward from the page's location.
The named component must be visible at the page's scope or at a level above it, such as a shared root.
See :ref:`topics-components` for the scope rules and :ref:`components-folder-discovery` for how the backend finds a component.

The recommended home for a reusable field component is a shared components root, the directory configured under ``DIRS`` in ``NEXT_FRAMEWORK["COMPONENT_BACKENDS"]``.
Components in a ``DIRS`` root are visible from every template, so one ``input`` component serves every form in the project.
The directory name is up to the project, conventionally something like ``_shared/_components``.
A page-local component placed in the page's own component folder also works when the field is only used on that one page.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [str(BASE_DIR / "_shared" / "_components")],
               "COMPONENTS_DIR": "_components",
           }
       ]
   }

The ``next.W054`` system check warns at startup when a ``ComponentWidget`` references a component that does not resolve.
It is a warning rather than an error because the component may come from an app imported later in the boot sequence.
A reference that still fails to resolve at render time raises ``RuntimeError``.

Before and After
----------------

Without ``ComponentWidget``, a form file carries a per-file ``INPUT_CLASS`` string and wraps it in Django widgets.
Every form that wants the same look copies the constant.

.. code-block:: python
   :caption: before — the class string lives in the form file

   INPUT_CLASS = (
       "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
       "focus:outline-none focus:ring-2 focus:ring-indigo-400"
   )

   class ArticleCreateForm(next.forms.Form):
       slug = django_forms.SlugField(
           widget=django_forms.TextInput(attrs={"class": INPUT_CLASS}),
       )
       title = django_forms.CharField(
           widget=django_forms.TextInput(attrs={"class": INPUT_CLASS}),
       )

.. code-block:: python
   :caption: after — the field names the shared component

   class ArticleCreateForm(next.forms.Form):
       slug = next.forms.CharField(widget=ComponentWidget("input", placeholder="wiki-slug"))
       title = next.forms.CharField(widget=ComponentWidget("input", placeholder="Title"))

The Tailwind classes now live once in the shared ``component.djx``.
The ``INPUT_CLASS`` constant disappears from the form file, and a styling change happens in one place.

When Not To Use It
------------------

Reach for a plain Django widget when it is simpler.
A hidden field, a checkbox, or a select with no custom styling needs no component, and a stock widget keeps the form shorter.

``ComponentWidget`` does not support :class:`~django.forms.MultiWidget` composition.
A field that splits across several controls, such as a split date and time input, stays on a Django ``MultiWidget``.
One ``ComponentWidget`` renders one component, so model a multi-control field with a regular widget instead.

A few field types are unsupported because their value semantics need behaviour the widget does not implement.

- A :class:`~django.forms.FileField` or :class:`~django.forms.ImageField` needs a multipart enctype that the widget does not request.
- A :class:`~django.forms.MultiValueField` such as a split date and time needs value decompression across several controls.
- A :class:`~django.forms.SelectMultiple` and a checkbox or boolean field need multi-value or omitted-value handling the widget does not perform.

The ``next.W055`` system check warns at startup when a ``ComponentWidget`` is attached to a ``FileField`` or a ``MultiValueField``, the cases where the mismatch silently loses data.

The widget renders through next.dj's component runtime and bypasses Django's form renderer, so the project's ``FORM_RENDERER`` theming does not apply, and widget introspection through ``subwidgets`` or a ``BoundWidget`` does not reflect the rendered output.
This is the intended contract, since the component is itself the rendering and theming layer.

See Also
--------

.. seealso::

   :ref:`topics-components` for component scope and visibility.
   :doc:`templates` for the ``{% form %}`` tag that renders the field.
   :doc:`modelforms` for the ``Meta.widgets`` mapping on a ModelForm.
   :doc:`/content/ref/forms` for the public forms API.
   :doc:`Django widgets <django:ref/forms/widgets>` for the underlying widget contract.
