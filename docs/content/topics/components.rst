.. _topics-components:

Components
==========

A component is a reusable template fragment with optional Python context.
Components live in folders under a configured components root and the framework discovers them by name.
This page covers the two component shapes, the rules for props and slots, how component context is resolved, how to ship co-located CSS and JS, and how to compose several components into larger UI.

.. contents::
   :local:
   :depth: 2

Overview
--------

The components backend scans every page tree for folders that match the configured components directory.
Each component lives in its own folder and has a name that comes from the folder.
A template references a component by name through the ``{% component "name" %}`` tag.

Components compose freely.
A page template can call a component, a component template can call another component, and a layout can call any component that is in scope.

Component Shapes
----------------

The backend recognises two shapes.

Simple component.
   A single ``component.djx`` file in a component folder.
   The folder name is the component name.
   No Python module is required.

Composite component.
   A folder with both ``component.djx`` and ``component.py``.
   The Python module declares context functions and optional render logic.
   The folder name remains the component name.

The two shapes share the same template syntax and the same call form.
Composite components add Python logic when the template needs computed values that go beyond the surrounding template context.

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

Component Folder Discovery
--------------------------

The components backend reads ``COMPONENTS_DIR`` from the first entry of ``DEFAULT_COMPONENT_BACKENDS``.
The default value is ``_components``.
The router also reads that name to skip those directories during URL scanning.

The backend recognises three sources for components.

App page trees.
   Every page tree that the router walks is also walked by the components backend.
   A folder named ``COMPONENTS_DIR`` under that tree is a components root for the application.

Project directories.
   The ``DIRS`` list adds absolute or project-relative roots that contribute global components.
   Components in these roots are visible from every template.

Custom backends.
   Additional entries in ``DEFAULT_COMPONENT_BACKENDS`` can serve components from any other source.
   See :doc:`extending` for the contract.

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [str(BASE_DIR / "shared_components")],
               "COMPONENTS_DIR": "_components",
           }
       ]
   }

Components Scope
----------------

Components are resolved through a scope tree.
A template only sees components from its own branch of the tree plus any project-level roots in ``DIRS``.
This prevents accidental collisions across apps that happen to use the same name.

Two scope rules apply.

Local scope.
   Components in the same page tree are visible from every template in that tree.

Global scope.
   Components in directories listed under ``DIRS`` are visible from every template, regardless of tree.

Two components with the same name in different scopes are valid only when one is local to a tree and the other is in another tree.
Two components with the same name in the same scope are reported by ``next.E020`` and ``next.E034`` system checks.

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
Single line, no children.

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

Props
-----

Props are literal strings or numbers.
The framework never evaluates ``some_var`` as a Python expression.
A prop value of ``some_var`` becomes the literal string ``"some_var"``, not the value of a template variable.

.. code-block:: jinja
   :caption: literal vs variable

   {% component "card" title="Hello" %}             {# title = "Hello" #}
   {% component "card" title=note.title %}          {# title resolves from context #}

The ``key=expression`` form does resolve against the template scope.
Use it when the value must come from a loop variable, the URL kwargs, or another context entry.

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

A caller-supplied ``{% #slot %}`` wins over the component's fallback body.
When the caller omits the slot the component renders its own ``{% #set_slot %}`` default instead.
Slot content reaches the component template under the ``slot_<name>`` key, so ``{% set_slot "content" %}`` and ``{{ slot_content }}`` resolve the same value.

Component Context
-----------------

A ``component.py`` next to ``component.djx`` runs Python code for the component.
Use ``@component.context("name")`` to publish named values that the template can render.

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

Component context functions take :doc:`DI parameters <dependency-injection>` the same way page context does.
The framework resolves parameters from the surrounding template scope, from URL kwargs, from the request, or from any registered provider.

Inherited Component Context
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Component context is local to the component by default.
Pass ``inherit_context=True`` to make the value available from nested component calls and from slot content.

.. code-block:: python
   :caption: shared component context

   @component.context("brand", inherit_context=True)
   def brand() -> str:
       return "Notes"

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

The components backend imports every discovered ``component.py`` on startup so the side effects of ``@component.context`` and ``@action`` are visible from the first request.

Lazy Loading
~~~~~~~~~~~~

For very large projects defer Python module loading until the first call to a component.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "LAZY_COMPONENT_MODULES": True,
   }

The filesystem scan still happens at startup so the scope tree is ready.
Python execution moves to the first component render.

Hot Reload
----------

The development server picks up new and changed component folders without a restart.
Each component root contributes its own watch spec.
A change inside the folder fires the autoreload pipeline and the runserver reloads with the updated component set.

The framework also emits two signals during the lifecycle.

- ``component_registered`` fires when an individual component enters the registry.
- ``components_registered`` fires once after a bulk discovery, with a list of every component that registered during the cycle.

System Checks
-------------

The components subsystem contributes Django system checks.

- ``next.E020`` reports two components with the same name in the same tree.
- ``next.E034`` reports global components with the same name across project roots.

Run them with ``uv run python manage.py check``.

Common Patterns
---------------

Wrapper Component
~~~~~~~~~~~~~~~~~

Use a block component with a single ``content`` slot to wrap arbitrary child markup.
Common shapes are cards, alerts, and dialogs.

Composite Component With Context
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a ``component.py`` when the template needs values computed from the surrounding context.
The Python module keeps the template free of business logic.

Shared UI Kit
~~~~~~~~~~~~~

Ship a folder of reusable components under a project directory listed in ``DIRS``.
Every application sees the same set, which keeps the design system consistent.
See :doc:`multi-project` for the multi project version of this pattern.

Conditional Rendering
~~~~~~~~~~~~~~~~~~~~~

A composite component can define a ``render`` function in ``component.py``.
The function receives DI-resolved parameters and returns the component body as a string.
Return an empty string to render nothing, which turns the component into a server side gate.

.. code-block:: python
   :caption: _components/feature_guard/component.py

   from next.components import component


   def render(flag_enabled: bool = False) -> str:
       if not flag_enabled:
           return ""
       return "<div class='feature'>New feature</div>"

See ``examples/feature-flags`` for a feature guard built this way.

See Also
--------

.. seealso::

   :doc:`context` for the difference between page and component context.
   :doc:`static-assets/index` for the static collector that emits component CSS and JS.
   :doc:`/content/howto/build-a-composite-component` for a recipe.
   :doc:`/content/internals/component-pipeline` for the discovery and render pipeline.
   :doc:`/content/ref/components` for the public API.
