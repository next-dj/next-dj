.. _topics-components:

Components
==========

A component is a reusable template fragment with optional Python context.
Components live in folders under a configured components root and the framework discovers them by name.

.. contents::
   :local:
   :depth: 2

Overview
--------

Components compose freely.
A page template can call a component, a component template can call another component, and a layout can call any component that is in scope.
The :ref:`components-folder-discovery` section below covers how the default ``FileComponentsBackend`` finds them.

Component shapes
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

Component folder discovery
--------------------------

The URL router reads the component folder name from the ``COMPONENTS_DIR`` key of the first ``COMPONENT_BACKENDS`` entry and treats every directory with that name as a component namespace.
``FileComponentsBackend`` itself never reads the key.
The key is required in every entry, an entry that omits it is reported by the ``next.E031`` system check, and only the value of the first entry takes effect.

When the URL router walks the page trees it skips directories that match a configured ``COMPONENTS_DIR``, so component folders never become URL segments.

The backend recognises three sources for components.

App page trees.
   As the URL router walks each page tree it calls ``register_components_folder_from_router_walk`` once per ``COMPONENTS_DIR`` folder it encounters.
   The helper offers the folder to each configured backend in order through ``ComponentsBackend.register_walked_folder`` and stops at the first that claims it, and it deduplicates by resolved path, so a folder seen twice is registered only once.
   ``FileComponentsBackend`` claims every folder it is offered, so the first file-sourced backend takes it.
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
   See `Component backends`_ below for the contract.

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

Component scope
---------------

Components are resolved through a scope tree.
A template only sees components from its own branch of the tree plus any project-level roots in ``DIRS``.
This prevents accidental collisions across apps that happen to use the same name.

Two scope rules apply.

Local scope.
   Components are visible to templates in their own directory and below, scoped by the folder that holds them.

Global scope.
   Components in directories listed under ``DIRS`` are visible from every template, regardless of tree.

Two components with the same name are valid when their scopes differ, for example one in a page tree and one in a ``DIRS`` root, or one at a tree root and one under a route below it.
One name in each of two page trees is valid too, because neither tree is visible from the other.
What is rejected is a name the resolver cannot decide: two components under one route scope (``next.E020``), or one name at the root scope of two ``DIRS`` roots, which are both visible everywhere (``next.E034``).
Both clashes are reported by system checks, covered in the `System checks`_ section below.

Calling a component
-------------------

Use the ``{% component %}`` tag.
The first argument is the component name.
Remaining arguments are ``key=value`` props.

.. code-block:: jinja
   :caption: calling a simple component

   {% component "card" title="Hello" %}

Void form
~~~~~~~~~

The tag has no closing form when the component does not take slots.
The void form fits on one line and accepts no child markup.
Use the block form covered under :ref:`components-multiline-tags` when the component renders slot content.

.. code-block:: jinja
   :caption: void form

   {% component "button" text="Save" variant="default" %}

Block form
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

Free children
~~~~~~~~~~~~~

Child markup placed inside a ``{% #component %}`` block without a wrapping ``{% #slot %}`` reaches the component template under the ``children`` context variable.
The component template renders ``{{ children }}`` to splice the content in.

.. code-block:: jinja
   :caption: free children

   {% #component "card" %}
     <p>Free markup with no slot wrapper.</p>
   {% /component %}

The ``card`` template renders this content wherever it places ``{{ children }}``.

The markup is spliced in as written, exactly like slot content.
Free children are finished HTML by the time the component runs, so escaping them a second time would only print the tags.
The calling template, not the component, decides what happens to a variable inside that markup, so a caller that turns escaping off with ``{% autoescape off %}`` hands the component whatever the block produced.
Values that arrive as props are a separate channel and stay autoescaped, so untrusted text belongs in a prop rather than in the child markup.

.. _components-multiline-tags:

Multiline tags
~~~~~~~~~~~~~~

Both the void form and the block form accept line breaks inside the tag body, which is useful when a component takes many props.
The framework enables ``re.DOTALL`` for Django's tag lexer at startup, so tag bodies wrap across lines in every template type.

.. warning::

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

Variable forwarding
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
The void caller slot marks the slot explicitly empty and suppresses the ``{% #set_slot %}`` fallback body.
Caller slot content reaches the component scope under the ``slot_<name>`` key.

Component context
-----------------

A ``component.py`` next to ``component.djx`` runs Python code for the component.
Use ``@component.context("key")`` to publish a value under that key for the template to render.

.. code-block:: python
   :caption: _components/note_card/component.py

   from notes.models import Note
   from next import component

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
   These are the registration failures, and the unkeyed form raises one more ``ValueError`` at render time, described below.

Pass ``serialize=True`` and optionally ``serializer=`` to include the return value in ``window.Next.context``.
The behaviour is identical to ``@context`` on a page module, so the value must be JSON-encodable by the active serializer.
See :doc:`static-assets/js-context` for the serialization options and :ref:`Serialization for the browser <topics-context-serialization>` for the encodability contract.

An unkeyed ``@component.context`` returning a dict serializes each key of that dict separately.
A ``serializer=`` on such an unkeyed callable applies to every key of the returned dict.
A keyed ``@component.context`` serializes its return value under the given key.
An unkeyed callable that returns anything other than a mapping is silently dropped from the template scope.

An unkeyed ``@component.context`` merges its dict into the component scope, so the framework guards the names that merge would quietly take over.
The render raises ``ValueError`` when the returned dict carries a prop passed by the ``{% component %}`` tag being rendered, a reserved render key, or any key that starts with ``slot_``.
The reserved render keys are ``children``, ``request``, ``csrf_token``, ``current_template_path``, ``current_page_module_path``, ``current_component_module_path``, ``_static_collector``, and ``_component_props``.
The whole ``slot_`` prefix is reserved, so a returned ``slot_count`` raises even when the component declares no ``count`` slot.
Register the value under an explicit ``@component.context("key")`` instead, which is the deliberate override and is never guarded.

An ordinary page context key that reaches the component through the surrounding scope stays outside the guard, so an unkeyed dict may still shadow it for the component body.
The ``{% component %}`` tag publishes the prop names of its own call site, ``ComponentWidget`` publishes the names it fills for its field, and ``render_component_by_name`` publishes the keys of its ``props`` mapping while leaving its ``context`` mapping ambient, so a bare ``render_component`` is the one path left guarding the reserved keys alone.
The same component code therefore raises under ``{% component "note_card" preview=text %}`` and merges quietly under ``{% component "note_card" %}``.
This failure leaves the render rather than degrading to an empty string, so ``STRICT_LOADING`` and ``DEBUG`` do not change it.

Co-located static assets
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

Module loading
--------------

By default the framework imports every ``component.py`` from each ``DIRS`` root during component backend setup.
``import_component_modules`` imports every ``component.py`` the backend has registered by the time it runs, both the ``DIRS`` roots and the page-tree folders the router walk claimed, and returns the paths it imported.
The bulk import runs the side effects of ``@component.context`` so they are visible from the first request.
A ``component.py`` may also register a form action by importing ``action`` from ``next.forms`` and applying ``@action``, which the same import makes visible.
See :doc:`/content/topics/forms/actions` for the action decorator.

Page-tree ``component.py`` modules follow a different path.
The URL router walks each page tree and ``register_components_folder_from_router_walk`` imports every ``component.py`` it registers inline.
They are available regardless of ``LAZY_COMPONENT_MODULES``.

The ``LAZY_COMPONENT_MODULES`` flag gates the ``DIRS`` bulk import only.
When the flag is set the framework skips that step and imports a ``component.py`` on first resolve instead.
A composite component whose template body lives in the module-level ``component`` string is still imported during discovery, because the scanner must read that attribute.
See :ref:`ref-settings` for the exact behaviour.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "LAZY_COMPONENT_MODULES": True,
   }

The render function
-------------------

A composite component can define a ``render`` function in ``component.py`` that returns the component body as a string in place of the template.
The function receives DI-resolved parameters drawn from the surrounding template scope, including props and page context variables.
Return an empty string to render nothing, which turns the component into a server side gate.

A ``render`` function takes over completely.
The component template, the lazy ``csrf_token``, and the ``@component.context`` callables do not run for that component.
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

Hot reload
----------

The development server reloads when a ``component.py`` changes inside a watched component folder.
The watched folders are the ``DIRS`` roots configured on a backend and the page-tree component folders the URL router walks.
Template-only edits to ``.djx`` files are reflected on the next request without a process restart.

Component backends
------------------

``COMPONENT_BACKENDS`` lists the sources the framework asks for components.
A backend subclasses ``next.components.ComponentsBackend`` and receives its whole settings entry as the single constructor argument.
Every entry declares ``BACKEND``, ``DIRS``, and ``COMPONENTS_DIR``, and ``next.E031`` reports an entry that omits one of them.

A backend answers with ``ComponentInfo`` records and never renders.
``ComponentsManager`` owns the render pipeline and shares it across every configured backend, which is why the contract asks for only two methods.

Required methods
~~~~~~~~~~~~~~~~

``get_component(name, template_path)``.
   Return the ``ComponentInfo`` this backend holds for ``name`` as seen from ``template_path``, or ``None`` to pass the name on.
   The manager consults backends in configuration order and takes the first record that is not ``None``.

``collect_visible_components(template_path)``.
   Return the mapping of every component name this backend makes visible from ``template_path``.
   The manager merges those mappings in configuration order, and the first backend to claim a name keeps it.

A ``ComponentInfo`` carries the component name, its scope root and scope-relative path, an optional template path, an optional module path, and the simple or composite flag.
The template body is read from a ``.djx`` file at the template path, or from a module-level ``component`` string in the module at the module path.
A backend that stores markup anywhere else materialises it in one of those two places before handing the record over.

Optional hooks
~~~~~~~~~~~~~~

The remaining five methods carry defaults that decline.
Leaving a hook alone is a supported answer, and it keeps the backend out of the behaviour that hook feeds.
A backend that resolves names on demand implements the two required methods and touches none of these.

``discover()``.
   Populate the backend from its source.
   The framework calls it on every configured backend once during application startup.
   The default does nothing, which suits a backend that resolves names lazily.

``import_component_modules()``.
   Execute the Python module of every known component and return the paths imported.
   It is separate from ``discover`` because ``LAZY_COMPONENT_MODULES`` leaves those modules unexecuted, and a caller that reads decorator state cannot wait for a render.
   The default returns an empty tuple, which is also the right answer for a backend whose components carry no module.

``register_walked_folder(folder, pages_root, scope_relative)``.
   Claim a components folder the page-tree walk found by returning ``True``.
   The walk offers each folder to the backends in configuration order and stops at the first that claims it, so one folder belongs to exactly one backend.
   The default returns ``False`` and leaves page-tree folders to another backend.

``iter_components()``.
   Return every component this backend has registered.
   The system checks read it to report duplicate names and wrong-decorator modules, which the render contract alone cannot answer.
   The default returns an empty iterable and keeps the backend out of those reports.

``global_component_roots()``.
   Return the scope roots whose root-scope components resolve from every template.
   The cross-root name check reads it to tell a shared root from a page tree.
   The default returns an empty iterable.

A worked backend
~~~~~~~~~~~~~~~~

This backend serves one flat folder of ``.djx`` files as globally visible components.
It implements the two required methods, populates itself in ``discover``, and opts into the two enumeration hooks so the system checks see its components.

.. code-block:: python
   :caption: notes/backends.py

   from pathlib import Path
   from typing import Any

   from next.components import ComponentInfo, ComponentsBackend

   class UiKitBackend(ComponentsBackend):
       """Serve a flat folder of .djx files as global components."""

       def __init__(self, config: dict[str, Any]) -> None:
           self._root = Path(config["DIRS"][0])
           self._components: dict[str, ComponentInfo] = {}

       def discover(self) -> None:
           for djx in sorted(self._root.glob("*.djx")):
               self._components[djx.stem] = ComponentInfo(
                   name=djx.stem,
                   scope_root=self._root,
                   scope_relative="",
                   template_path=djx,
                   module_path=None,
                   is_simple=True,
               )

       def get_component(self, name: str, template_path: Path) -> ComponentInfo | None:
           return self._components.get(name)

       def collect_visible_components(
           self, template_path: Path
       ) -> dict[str, ComponentInfo]:
           return dict(self._components)

       def iter_components(self) -> list[ComponentInfo]:
           return list(self._components.values())

       def global_component_roots(self) -> list[Path]:
           return [self._root]

Register it after the default backend so page-tree and ``DIRS`` components resolve first and this one answers the names they do not hold.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_components",
           },
           {
               "BACKEND": "notes.backends.UiKitBackend",
               "DIRS": [str(BASE_DIR / "ui_kit")],
               "COMPONENTS_DIR": "_components",
           },
       ]
   }

Only the ``COMPONENTS_DIR`` value of the first entry takes effect, because the URL router reads the folder name it skips from that entry alone.
The ``component_backend_loaded`` signal fires once per backend instance with ``config`` and ``instance``, so a receiver can inspect what was built.

Lifecycle signals
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
     - The component backend class
     - ``config``, ``instance``
   * - ``component_rendered``
     - ``ComponentsManager``
     - ``info``, ``template_path``

See :doc:`/content/ref/signals` for the full signal catalog.

System checks
-------------

The components subsystem contributes Django system checks.

- ``next.E020`` reports two components with the same name under one route scope, where nothing tells them apart.
  Rename one of the colliding components or move it under a route scope of its own.
- ``next.E021`` reports a ``component.py`` that uses the page ``context`` decorator, whether it comes from ``next.pages`` or from the curated ``next`` root.
  Use ``@component.context`` from ``next.components`` instead.
- ``next.E034`` reports one name at the root scope of two roots the same template resolves against with neither taking precedence.
  Rename one of the colliding components or move it under a route scope.
- ``next.E075`` reports a ``@component.context`` registration bound to a file no component render collects.
  Decorate callables defined in the ``component.py`` itself.

Run them with ``uv run python manage.py check``.
The full catalog lives in :doc:`/content/ref/system-checks`.

Common patterns
---------------

Three patterns build on the sections above.

- Wrap arbitrary child markup with a block component that has a single ``content`` slot, as in cards, alerts, and dialogs.
- Add a ``component.py`` when the template needs values computed from the surrounding context, see :doc:`/content/howto/build-a-composite-component`.
- Ship a folder of reusable components under a ``DIRS`` root for a shared UI kit, see :doc:`multi-project` and :doc:`/content/misc/examples`.

See also
--------

.. seealso::

   :doc:`context` for the difference between page and component context.
   :doc:`file-router` for the URL router walk that registers app page-tree component folders.
   :doc:`static-assets/index` for the static collector that emits component CSS and JS.
   :doc:`/content/howto/build-a-composite-component` for a recipe.
   :doc:`/content/internals/component-pipeline` for the discovery and render pipeline.
   :doc:`/content/ref/components` for the public API.
