.. _topics-components:

Components
==========

A component is a reusable template fragment with optional Python context.
Components live in folders under a configured components root and the framework discovers them by name.
This page covers the two component shapes, the rules for props and slots, how component context is resolved, how to ship co-located CSS and JS, and how to compose several components into a larger interface.

.. contents::
   :local:
   :depth: 2

Overview
--------

Components compose freely.
A page template can call a component, a component template can call another component, and a layout can call any component that is in scope.
The :ref:`components-folder-discovery` section below covers how the default ``FileComponentsBackend`` finds them.

Component Shapes
----------------

The backend recognises two shapes.

Simple component.
   A single ``.djx`` file placed directly in a component namespace directory.
   The file stem is the component name.
   No Python module is required.

Composite component.
   A folder containing ``component.py``, ``component.djx``, or both.
   The Python module declares context functions and optional render logic.
   The folder name remains the component name.

The two shapes share the same template syntax and the same call form.
Composite components add Python logic when the template needs computed values that go beyond the surrounding template context.

.. note::

   A composite component may supply its template body from Python instead of a ``component.djx`` file.
   When ``component.py`` exposes a module-level string named ``component`` and no ``component.djx`` file exists, the framework uses that string as the template body.
   ``ComponentScanner`` registers the component with ``template_path`` pointing at the ``component.py`` file itself, and ``ComponentTemplateLoader.load`` reads the ``component`` attribute from that module.
   A ``component.py`` with neither a ``render`` function nor a ``component`` string and no ``component.djx`` alongside it produces a component that renders nothing.

.. code-block:: text
   :caption: component folder layouts

   _components/
     card/
       component.djx
     note_card/
       component.djx
       component.py
       component.css
       component.js
     button.djx

.. _components-folder-discovery:

Component Folder Discovery
--------------------------

Each entry in ``COMPONENT_BACKENDS`` carries its own ``COMPONENTS_DIR`` name, defaulting to ``_components``.
The components backend treats every directory with that name as a component namespace.

When the URL router walks the page trees it skips directories that match a configured ``COMPONENTS_DIR``, so component folders never become URL segments.

The backend recognises three sources for components.

App page trees.
   As the URL router walks each page tree it calls ``register_components_folder_from_router_walk`` once per ``COMPONENTS_DIR`` folder it encounters.
   The helper registers that folder into the first ``FileComponentsBackend`` and deduplicates by resolved path, so a folder seen twice is registered only once.
   A folder named ``COMPONENTS_DIR`` under that tree is a components root for the application.

   .. note::

      When several ``FileComponentsBackend`` entries are configured, ``register_components_folder_from_router_walk`` registers app page-tree folders into the first one only.
      Configure additional ``FileComponentsBackend`` instances through ``DIRS`` so they pick up their own roots independently.

Project directories.
   The ``DIRS`` list adds absolute or project-relative roots that contribute global components.
   Components in these roots are visible from every template.
   The scanner only inspects the immediate children of each root, so place every component folder or ``.djx`` file directly under the ``DIRS`` entry rather than in nested sub-folders.

Custom backends.
   Additional entries in ``COMPONENT_BACKENDS`` can serve components from any other source.
   See :doc:`extending` for the contract.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [str(BASE_DIR / "shared_components")],
               "COMPONENTS_DIR": "_components",
           }
       ]
   }

Component Scope
---------------

Components are resolved through a scope tree.
A template only sees components from its own branch of the tree plus any project-level roots in ``DIRS``.
This prevents accidental collisions across apps that happen to use the same name.

Two scope rules apply.

Local scope.
   Components are visible to templates in their own directory and below, scoped by the folder that holds them.

Global scope.
   Components in directories listed under ``DIRS`` are visible from every template, regardless of tree.

Two components with the same name in different scopes are valid only when one is local to a tree and the other is in another tree.
The same-scope name clash is reported by a system check, covered in the `System Checks`_ section below.

Calling a Component
-------------------

Use the ``{% component %}`` tag.
The first argument is the component name.
Remaining arguments are ``key=value`` props.

.. code-block:: jinja
   :caption: calling a simple component

   {% component "card" title="Hello" %}

Void Form
~~~~~~~~~

The tag has no closing form when the component does not take slots.
The void form fits on one line and accepts no child markup.
Use the block form covered under :ref:`components-multiline-tags` when the component renders slot content.

.. code-block:: jinja
   :caption: void form

   {% component "button" text="Save" variant="default" %}

Block Form
~~~~~~~~~~

Prepend a hash sign to open a block.
Pair it with the matching close tag.

.. code-block:: jinja
   :caption: block form

   {% #component "card" title="Welcome" %}
     {% #slot "content" %}
       <p>Some content inside the card.</p>
     {% /slot %}
   {% /component %}

The block form lets the component template substitute child content through slots.

Free Children
~~~~~~~~~~~~~

Child markup placed inside a ``{% #component %}`` block without a wrapping ``{% #slot %}`` reaches the component template under the ``children`` context variable.
The component template renders ``{{ children }}`` to splice the content in.

.. code-block:: jinja
   :caption: free children

   {% #component "card" %}
     <p>Free markup with no slot wrapper.</p>
   {% /component %}

The ``card`` template renders this content wherever it places ``{{ children }}``.

.. _components-multiline-tags:

Multiline Tags
~~~~~~~~~~~~~~

Both the void form and the block form accept line breaks inside the tag body, which is useful when a component takes many props.
The framework enables ``re.DOTALL`` for Django's tag lexer at startup, so tag bodies wrap across lines in every template type.

.. caution::

   This changes template parsing for **every** template the process loads, not only DJX files.
   If you rely on Django's stock behaviour where a newline inside ``{% ... %}`` ends the tag, adjust those templates before adopting next.dj.
   The patch is applied once at import time and is one-way, so the original Django pattern is not restored when the components template tag library is unloaded.

.. code-block:: jinja
   :caption: multiline void tag

   {% component "card"
      title="Welcome"
      variant="featured"
      pinned=True %}

.. code-block:: jinja
   :caption: multiline block tag

   {% #component "card"
      title="News"
      variant="featured" %}
     {% #slot "content" %}
       <p>Latest update.</p>
     {% /slot %}
   {% /component %}

Props
-----

Each ``key=value`` prop is compiled as a Django template expression and resolved against the current template context at render time.
A prop value may be one of the following.

Quoted string literal.
   ``title="Hello"`` passes the string ``Hello``.
   Double or single quotes both work.

Number literal.
   ``count=3`` and ``rating=4.5`` pass the integer and float.

Boolean literal.
   ``pinned=True`` and ``pinned=False`` pass the boolean.

Template expression.
   An unquoted token is resolved against the surrounding context, exactly like ``{{ ... }}``.
   ``title=note.title`` performs the attribute lookup, ``count=notes|length`` applies a filter.
   When the lookup fails the prop resolves to the empty string.

.. code-block:: jinja
   :caption: literal vs context lookup

   {% component "card" title="Hello" %}
   {% component "card" title=note.title %}
   {% component "card" pinned=True %}

A quoted string prop is always passed as an unescaped plain string.
The component template autoescapes it through ``{{ prop }}``, so pass ``prop=value|safe`` or an already-safe variable when the component must receive raw HTML.

Variable Forwarding
~~~~~~~~~~~~~~~~~~~

A component receives every variable that is in scope at the call site.
Inside a ``{% for note in notes %}`` loop the variable ``note`` is forwarded into the component automatically.
Templates rarely need to pass loop variables explicitly.

.. code-block:: jinja
   :caption: implicit forwarding

   {% for note in notes %}
     {% component "note_card" %}
   {% endfor %}

The ``note_card`` template can reference ``{{ note }}`` directly.

Slots
-----

A slot is a named area inside a component template that the caller fills with content.
The component template marks the slot location with ``{% set_slot %}``.
The caller fills it with ``{% #slot %}`` inside a ``{% #component %}`` block.

The component template uses the short void form of ``{% set_slot %}`` for a slot with no default, or the block form to declare a fallback body.

.. code-block:: jinja
   :caption: _components/card/component.djx

   <article class="card">
     {% if title %}<h2>{{ title }}</h2>{% endif %}
     {% #set_slot "content" %}<p>Nothing here yet.</p>{% /set_slot %}
   </article>

Callers fill the slot with ``{% #slot %}`` inside the block form of ``{% component %}``.

.. code-block:: jinja
   :caption: filling a slot

   {% #component "card" title="News" %}
     {% #slot "content" %}
       <p>Latest update.</p>
     {% /slot %}
   {% /component %}

A caller-supplied slot replaces the component's ``{% #set_slot %}`` fallback body.
When the caller omits the slot the fallback renders.

Both the void ``{% slot "name" %}`` and the block ``{% #slot %}`` forms are supported on the caller side, mirroring the void and block split shown for ``{% component %}``.
The void form suits a slot whose value comes from a prop or is left empty.
Caller slot content reaches the component scope under the ``slot_<name>`` key.

Component Context
-----------------

A ``component.py`` next to ``component.djx`` runs Python code for the component.
Use ``@component.context("key")`` to publish a value under that key for the template to render.

.. code-block:: python
   :caption: _components/note_card/component.py

   from notes.models import Note
   from next.components import component

   @component.context("preview")
   def preview(note: Note) -> str:
       words = note.body.split()
       return " ".join(words[:12])

   @component.context("href")
   def href(note: Note) -> str:
       return f"/notes/{note.id}/"

The matching template renders the published keys.

.. code-block:: jinja
   :caption: _components/note_card/component.djx

   <a href="{{ href }}">{{ note.title }}</a>
   <p>{{ preview }}</p>

Component context functions take :doc:`DI parameters <dependency-injection>` the same way page context does.
The framework resolves parameters from the surrounding template scope, from URL kwargs, from the request, or from any registered provider.

.. note::

   Registration raises ``ValueError`` for a key reserved for dependency injection, such as ``request``.
   It also raises ``ValueError`` for a duplicate registration of two different functions under the same key, or of two different unkeyed callables, in one ``component.py``.
   Re-registering the same function under the same key replaces the stored entry rather than raising.

Pass ``serialize=True`` and optionally ``serializer=`` to include the return value in ``window.Next.context``.
The behaviour is identical to ``@context`` on a page module, so the value must be JSON-encodable by the active serializer.
See :doc:`static-assets/js-context` for the serialization options and :ref:`Serialization for the Browser <topics-context-serialization>` for the encodability contract.

An unkeyed ``@component.context`` returning a dict serializes each key of that dict separately.
A keyed ``@component.context`` serializes its return value under the given key.
An unkeyed callable that returns anything other than a mapping is silently dropped from the template scope.

Co-located Static Assets
------------------------

A component folder can ship its own CSS, JS, and ECMAScript modules.

.. code-block:: text
   :caption: component layout with assets

   _components/note_card/
     component.djx
     component.py
     component.css
     component.js
     component.mjs

The static collector picks up each asset by stem.
``component.css`` becomes a ``<link>`` emitted by ``{% collect_styles %}``.
``component.js`` becomes a ``<script>`` emitted by ``{% collect_scripts %}``.
``component.mjs`` is emitted as ``<script type="module">``.

The collector emits each asset exactly once per request, even when multiple components reference the same file.
See :doc:`static-assets/deduplication` for the dedup rules.

Module Loading
--------------

By default the framework imports every ``component.py`` from each ``DIRS`` root during component backend setup.
``import_all_component_modules`` walks the registry built from those roots.
The bulk import runs the side effects of ``@component.context`` so they are visible from the first request.
A ``component.py`` may also register a form action with ``@action``, which the same import makes visible.
See :doc:`/content/topics/forms/actions` for the action decorator.

Page-tree ``component.py`` modules follow a different path.
The URL router walks each page tree and ``register_components_folder_from_router_walk`` imports every ``component.py`` it registers inline.
They are available regardless of ``LAZY_COMPONENT_MODULES``.

The ``LAZY_COMPONENT_MODULES`` flag gates the ``DIRS`` bulk import only.
When the flag is set the framework skips that step and imports a ``component.py`` on first resolve instead.
See :ref:`ref-settings` for the exact behaviour.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "LAZY_COMPONENT_MODULES": True,
   }

The Render Function
-------------------

A composite component can define a ``render`` function in ``component.py`` that returns the component body as a string in place of the template.
The function receives DI-resolved parameters drawn from the surrounding template scope, including props and page context variables.
The lazy ``csrf_token`` and any ``@component.context`` callables are not run on this path.
Return an empty string to render nothing, which turns the component into a server side gate.

A ``render`` function takes over completely.
The component template and ``@component.context`` callables do not run for that component.
The function may return a string or an :class:`~django.http.HttpResponse`.
When it returns an :class:`~django.http.HttpResponse`, the body is decoded as UTF-8 regardless of the response's ``Content-Type`` charset and spliced into the page.
The response status code and headers are not propagated.
Any other return value is coerced through ``str()`` before splicing.

When ``component.py`` defines no ``render`` function, the component renders its template and runs every ``@component.context`` callable as usual.

.. code-block:: python
   :caption: _components/feature_guard/component.py

   def render(flag_enabled: bool = False) -> str:
       if not flag_enabled:
           return ""
       return "<div class='feature'>New feature</div>"

Invoke the guard from a page template and forward the flag from the surrounding context.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   {% component "feature_guard" flag_enabled=flags.new_ui %}

See ``examples/feature-flags`` for a feature guard built this way.

Hot Reload
----------

The development server reloads when a ``component.py`` changes inside a watched component folder.
The watched folders are the ``DIRS`` roots configured on a backend and the page-tree component folders the URL router walks.
Template-only edits to ``.djx`` files are reflected on the next request without a process restart.

Lifecycle Signals
-----------------

The framework emits four signals during the component lifecycle.

.. list-table::
   :header-rows: 1
   :widths: 28 28 44

   * - Name
     - Sender
     - Keyword arguments
   * - ``component_registered``
     - ``ComponentRegistry``
     - ``info``
   * - ``components_registered``
     - ``ComponentRegistry``
     - ``infos``
   * - ``component_backend_loaded``
     - ``ComponentsManager``
     - ``backend``, ``config``
   * - ``component_rendered``
     - ``ComponentsManager``
     - ``info``, ``template_path``

See :doc:`/content/ref/signals` for the full signal catalog.

System Checks
-------------

The components subsystem contributes Django system checks.

- ``next.E020`` reports two components with the same name in the same scope.
  Rename one of the colliding components or move it to a different scope root.
- ``next.E034`` reports one component name used at the root route scope of more than one page tree.
  Rename one of the colliding components or move it to a different scope root.

Run them with ``uv run python manage.py check``.

Common Patterns
---------------

Three patterns build on the sections above.

- Wrap arbitrary child markup with a block component that has a single ``content`` slot, as in cards, alerts, and dialogs.
- Add a ``component.py`` when the template needs values computed from the surrounding context, see :doc:`/content/howto/build-a-composite-component`.
- Ship a folder of reusable components under a ``DIRS`` root for a shared UI kit, see :doc:`multi-project` and :doc:`/content/misc/examples`.

See Also
--------

.. seealso::

   :doc:`context` for the difference between page and component context.
   :doc:`file-router` for the URL router walk that registers app page-tree component folders.
   :doc:`static-assets/index` for the static collector that emits component CSS and JS.
   :doc:`/content/howto/build-a-composite-component` for a recipe.
   :doc:`/content/internals/component-pipeline` for the discovery and render pipeline.
   :doc:`/content/ref/components` for the public API.
