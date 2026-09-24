.. _topics-forms-field-components:

Field components
================

Changing an input's classes or accessibility markup across a project usually means editing every form or shadowing Django's widget templates, because a next.dj form pins its own renderer and the project-wide ``FORM_RENDERER`` setting never reaches it.
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

   from wiki.models import Article

   import next.forms
   from next.forms import ComponentWidget

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

The context contract
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

Ambient keys.
   The widget seeds the render frame of the surrounding render, naming the template the lookup ran from, the ``page.py`` of the page, the anchor the actions of the form resolve against, and the static collector.
   They belong to the reserved render keys rather than to the props of the call, so an unkeyed ``@component.context`` that returns one raises ``ValueError`` while the component body composes from them.
   The request is not seeded with them, the render stamps it instead, so a body reading ``request`` or spelling ``{% csrf_token %}`` needs the request the ``{% form %}`` tag passes to ``bind_component_widgets``.

``name``, ``value``, ``errors``, and ``attrs`` are reserved context keys.
The widget writes them last, so they always win over a same-named entry from ``attrs=`` or an extra keyword argument.

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

Scope and registration
----------------------

A ``ComponentWidget`` resolves its component the same way the ``{% component %}`` tag does, walking outward from the page's location.
The named component must be visible at the page's scope or at a level above it, such as a shared root.
See :ref:`topics-components` for the scope rules and :ref:`components-folder-discovery` for how the backend finds a component.
The anchor of that walk is the template of the page that rendered the form, and a nested tag inside the component resolves from the same anchor.

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
Both ``next.W054`` and the field-pairing check ``next.W055`` described under `When not to use it`_ walk the registered form-class actions only, so a ``ComponentWidget`` on a wizard step form or on a form marked ``Meta.abstract = True`` is never inspected and surfaces at render time instead.
A form built by a ``form_class`` factory is out of reach for the same reason, because the registry holds the callable rather than the class it returns.
A reference that still fails to resolve at render time raises ``next.forms.UnregisteredComponentError``, a ``LookupError`` subclass whose message names the search anchor and the closest visible component names.

.. _topics-forms-field-components-composition:

Composition inside a field component
------------------------------------

The template of a field component calls ``{% component %}`` like any other component template.
The nested reference resolves from the page anchor the widget searched from, so a component the page can see is a component the field component can see.
See :ref:`topics-components` for the scope rules and :ref:`components-folder-discovery` for how the backend finds the nested name.

The co-located assets of a nested component land in the document of the page that rendered the form, because the widget hands the component runtime the collector of that page.
A file the page already carries is registered once, see :doc:`/content/topics/static-assets/deduplication` for the dedup rules.

A ``@component.context(serialize=True)`` value inside a nested component reaches the js context of the page, the same payload a nested component publishes under a plain page render.
The value is serialised as it is collected, so it has to be JSON-serialisable or carry a serializer of its own, and one collector serves the page, so two fields rendering the same component publish one entry under the merge policy of the collector rather than one entry each.
See :ref:`topics-static-js-context` for how that payload reaches the browser.

A page-scoped ``{% form %}`` or ``{% action_url %}`` inside a field component finds its page, because the frame carries the path of the page module beside the template path.
It carries a second anchor as well, the one the ``{% form %}`` tag resolved for its own action, so an action registered on the ``component.py`` that holds the form is reachable from inside the field component too.
A lookup tries the ``component.py`` of the component being rendered, then that form anchor, and then the page.
See :doc:`templates` for the tags that read those anchors.

The body of a field component inherits the widget scope the way a component nested in a page inherits the page scope, so ``name``, ``value``, ``errors``, and every extra keyword of the widget are ambient names inside a component it nests.
A ``@component.context`` parameter of that nested component sharing one of those names is filled from the field state unless the call site passes a prop under the same name.

Before and after
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

.. _topics-forms-field-components-files:

File fields
-----------

A :class:`~django.forms.FileField` or :class:`~django.forms.ImageField` takes ``ComponentFileWidget``, a ``ComponentWidget`` that mixes in Django's :class:`~django.forms.FileInput`.
The widget therefore binds like the stock file control, reading its value from the uploaded files instead of the posted data and reporting the field as omitted only when no upload arrived under its name.
An :class:`~django.forms.ImageField` adds its ``accept="image/*"`` attribute as usual, and a subclass that sets ``allow_multiple_selected`` collects every upload under the name.
It sets ``needs_multipart_form``, so the ``{% form %}`` tag emits ``enctype="multipart/form-data"`` on its own.

.. code-block:: python
   :caption: a file field rendered through a component

   import next.forms
   from next.forms import ComponentFileWidget, ComponentWidget

   class AttachmentForm(next.forms.Form):
       title = next.forms.CharField(widget=ComponentWidget("input", placeholder="Title"))
       file = next.forms.FileField(widget=ComponentFileWidget("file-input"))

The component receives the same context as with ``ComponentWidget``.
``value`` is the stored file when there is one, an object with a ``url`` such as the :class:`~django.db.models.fields.files.FieldFile` of a model instance, and ``None`` otherwise.
An in-flight upload never reaches the component, because a browser never lets a server re-populate a file control, so a re-render after a failed submit still shows the stored file.
The ``required`` attribute is dropped once a file is stored, so editing an instance does not force a fresh upload.

.. code-block:: jinja
   :caption: a minimal file input component

   <input
     type="file"
     name="{{ name }}"
     {% if id %}id="{{ id }}"{% endif %}
     {% if required %}required{% endif %}
   />
   {% if value %}
     <a href="{{ value.url }}">{{ value.name }}</a>
   {% endif %}

Clearing a stored file is out of scope.
The checkbox that :class:`~django.forms.ClearableFileInput` adds has no counterpart here, so a field that needs it keeps the stock widget.

When not to use it
------------------

Reach for a plain Django widget when it is simpler.
A hidden field, a checkbox, or a select with no custom styling needs no component, and a stock widget keeps the form shorter.

``ComponentWidget`` does not support :class:`~django.forms.MultiWidget` composition.
A field that splits across several controls, such as a split date and time input, stays on a Django ``MultiWidget``.
One ``ComponentWidget`` renders one component, so model a multi-control field with a regular widget instead.

Two field shapes stay unsupported because their value semantics need behaviour the widgets do not implement.

- A :class:`~django.forms.MultiValueField` such as a split date and time needs value decompression across several controls.
- A :class:`~django.forms.SelectMultiple` and a checkbox or boolean field need multi-value or omitted-value handling the widgets do not perform.

The ``next.W055`` system check warns at startup for the file and multi-value pairings, where the mismatch silently loses data.
It reports a widget without multipart binding on a :class:`~django.forms.FileField`, a multipart widget such as ``ComponentFileWidget`` on a field that is not one, and any ``ComponentWidget`` on a :class:`~django.forms.MultiValueField`.
A file field takes ``ComponentFileWidget``, see `File fields`_.

The widget renders through next.dj's component runtime and bypasses Django's form renderer, so the project's ``FORM_RENDERER`` theming does not apply, and widget introspection through ``subwidgets`` or a ``BoundWidget`` does not reflect the rendered output.
This is the intended contract, since the component is itself the rendering and theming layer.

See also
--------

.. seealso::

   :ref:`topics-components` for component scope and visibility.
   :doc:`templates` for the ``{% form %}`` tag that renders the field.
   :doc:`modelforms` for the ``Meta.widgets`` mapping on a ModelForm.
   :doc:`/content/ref/forms` for the public forms API.
   :doc:`Django widgets <django:ref/forms/widgets>` for the underlying widget contract.
