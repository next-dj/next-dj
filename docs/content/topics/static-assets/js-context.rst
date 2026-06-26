.. _topics-static-js-context:

JavaScript Context
==================

next.dj ships a ``Next`` object to the browser that holds context values marked for serialisation.
This page covers how to opt a context value in, how to choose a serializer, and how the conflict policy resolves duplicate keys.

.. contents::
   :local:
   :depth: 2

The Next Object
---------------

The static manager injects a runtime script that defines ``window.Next`` before the collected scripts run.
Context values opted into serialisation land under ``window.Next.context``.

Opting In
---------

Pass ``serialize=True`` on the ``@context`` decorator, or on ``@component.context`` in a component.
See :doc:`/content/topics/context` for both decorators.
The value appears under ``window.Next.context.<key>``.
Keys without the flag stay server-side only.

A value the active serializer cannot encode raises ``TypeError`` during rendering, when the collector registers it.
The error names the offending key.
See :ref:`Serialization for the Browser <topics-context-serialization>` for the accepted shapes and the common materialisation patterns.

Serializers
-----------

A serializer turns a Python value into JSON text.
It implements the ``JsContextSerializer`` protocol, which has one method, ``dumps``.

The framework ships two implementations.

``JsonJsContextSerializer``.
   The process wide default.
   Encodes through Django ``DjangoJSONEncoder``.

``PydanticJsContextSerializer``.
   Encodes Pydantic models through ``model_dump``.
   Falls back to ``DjangoJSONEncoder`` for plain values.
   Requires the ``pydantic`` package.
   The class raises ``ImportError`` at construction when ``pydantic`` is not installed.

Project-Wide Serializer
-----------------------

Set ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` to the dotted path of a serializer class.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
   }

``resolve_serializer`` reads the setting on every call.
When the key is absent or set to an empty string the framework uses ``JsonJsContextSerializer``.

System Check
~~~~~~~~~~~~

The ``next.W042`` system check validates ``JS_CONTEXT_SERIALIZER`` at startup.
It warns under any of five conditions.

- The value is not a string.
- The dotted path cannot be imported.
- The resolved attribute is not a class.
- The class cannot be instantiated.
- The instance does not implement the ``JsContextSerializer`` protocol, a ``dumps(value) -> str`` method.

The check is skipped when the key is absent or set to an empty string.

Per-Key Serializer
------------------

Pass ``serializer=`` on a single ``@context`` decorator to route one key through a custom serializer.
The override applies only to that key.

.. code-block:: python
   :caption: per key serializer

   from next.pages import context
   from next.static import PydanticJsContextSerializer

   @context("featured", serialize=True, serializer=PydanticJsContextSerializer())
   def featured() -> object:
       return load_featured_note()

The collector routes the ``featured`` key through the supplied serializer and every other key through the project default.

The ``serializer=`` parameter takes an already-instantiated object.
``JS_CONTEXT_SERIALIZER`` in settings takes a dotted import path instead.

.. note::

   ``PydanticJsContextSerializer()`` imports pydantic at instantiation time.
   Creating an instance at module level raises ``ImportError`` on startup when pydantic is not installed.
   Use the ``JS_CONTEXT_SERIALIZER`` setting for a process-wide override that keeps the import lazy.

Writing a Serializer
--------------------

A serializer is any class with a ``dumps`` method.

.. code-block:: python
   :caption: notes/serializers.py

   import json
   from django.core.serializers.json import DjangoJSONEncoder

   class CompactSerializer:
       """Serialise context values with sorted keys for stable output."""

       def dumps(self, value: object) -> str:
           return json.dumps(
               value,
               cls=DjangoJSONEncoder,
               separators=(",", ":"),
               sort_keys=True,
           )

Point ``JS_CONTEXT_SERIALIZER`` at the dotted path of the class.
The framework instantiates it through ``resolve_serializer``.

Key Conflict Policy
-------------------

Two context functions can mark the same key for serialisation.
The collector resolves the conflict through a JS context policy.

The framework ships four policies in ``next.static.collector``.

``FirstWinsPolicy``.
   Keeps the first value, ignores later ones.

``LastWinsPolicy``.
   Keeps the last value.

``RaiseOnConflictPolicy``.
   Raises on a duplicate key.

``DeepMergePolicy``.
   Recursively merges nested dicts when both values are dicts.
   Overwrites the existing scalar with the latest value when either side is not a dict.

Configure the policy through the first static backend ``OPTIONS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "JS_CONTEXT_POLICY": "next.static.collector.DeepMergePolicy",
               },
           }
       ]
   }

The configured policy fires anywhere the same key reaches the collector twice, including page-to-component, component-to-component, page-to-layout, and any contributor that calls ``StaticCollector.add_js_context`` directly.
Two ``@context`` decorators on the same page that register the same key always resolve first-wins, regardless of ``JS_CONTEXT_POLICY``.
Pick distinct keys when both registrations live in the same module.

Writing a Policy
----------------

A custom policy implements the ``JsContextPolicy`` protocol from ``next.static.collector``.
The protocol has one method, ``merge(existing, key, value)``, which returns the updated context dict.

.. code-block:: python
   :caption: notes/policies.py

   from typing import Any

   class NamespacePolicy:
       """Group conflicting keys under a per-key list."""

       def merge(self, existing: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
           current = existing.get(key)
           if current is None:
               existing[key] = value
           elif isinstance(current, list):
               current.append(value)
           else:
               existing[key] = [current, value]
           return existing

Point ``JS_CONTEXT_POLICY`` in the first static backend ``OPTIONS`` at the dotted path of the class.

Reading on the Client
---------------------

Register the key server-side with ``serialize=True``.

.. code-block:: python
   :caption: notes/pages/page.py

   from next.pages import context
   from notes.models import Note

   @context("note_count", serialize=True)
   def note_count() -> int:
       return Note.objects.count()

Co-located JS and inline scripts then read the value under ``window.Next.context``.

.. code-block:: javascript
   :caption: notes/_components/note_card/component.js

   document.addEventListener("DOMContentLoaded", () => {
     const count = window.Next.context.note_count ?? 0;
     console.log(`There are ${count} notes.`);
   });

The runtime script defines ``window.Next`` before the collected scripts run.
The runtime script is always the first tag in the ``scripts`` slot, ahead of every co-located, module-list, and ``{% use_script %}`` asset, so any of those may safely read ``window.Next``.

Client Event API
~~~~~~~~~~~~~~~~~

The ``Next`` object exposes an event API alongside ``window.Next.context``.
Code that needs the context the moment it lands subscribes through ``Next.on`` rather than reading ``window.Next.context`` at an arbitrary time.

``Next.on(event, listener)`` registers a listener and returns an unsubscribe function.
The function removes that listener when called.
The ``listener`` receives the context object as its only argument and reads its values.

Two events fire.
The ``"context-updated"`` event fires whenever the framework loads a new context.
The ``"ready"`` event fires once the first context is loaded, and a listener registered after that point receives an immediate replay with the current context.

.. code-block:: javascript
   :caption: notes/_components/note_card/component.js

   const unsubscribe = window.Next.on("ready", (context) => {
     const count = context.note_count ?? 0;
     console.log(`There are ${count} notes.`);
   });

   window.Next.on("context-updated", (context) => {
     render(context);
   });

``Next.use(plugin)`` calls ``plugin`` with the ``Next`` object and returns whatever the plugin returns.
A plugin is any function that takes the ``Next`` object, so it can subscribe to events or read the context and expose its own helper.

.. code-block:: javascript
   :caption: notes/static/counter.js

   const counter = window.Next.use((next) => ({
     value: () => next.context.note_count ?? 0,
   }));

Runtime Script Options
----------------------

The setting ``NEXT_FRAMEWORK["NEXT_JS_OPTIONS"]`` is a dict that configures the runtime script builder.
The builder controls how and where the ``Next`` script is injected through a ``ScriptInjectionPolicy``.
An absent or empty ``NEXT_JS_OPTIONS`` uses the ``AUTO`` policy and the default tag templates.

.. list-table::
   :header-rows: 1
   :widths: 15 50 35

   * - Policy
     - Effect
     - When to use
   * - ``AUTO`` (default)
     - Injects the preload hint, the ``<script>`` tag, and the ``Next._init`` call into every rendered page.
     - Pages that read ``window.Next.context`` or use co-located JS.
   * - ``DISABLED``
     - Skips injection entirely. ``window.Next`` is not defined.
     - Pages that serve raw data or HTML fragments and have no client-side JS that reads ``window.Next``.
   * - ``MANUAL``
     - Skips automatic injection in the static manager, the same as ``DISABLED``.
       The script builder stays available for custom emission.
     - Pages where you control placement of the script tags in a layout template.

.. note::

   Under ``MANUAL`` the static manager skips both the preload hint and the ``Next._init`` wrap, exactly like ``DISABLED``.
   To inject ``window.Next`` yourself, resolve the runtime URL with ``staticfiles_storage.url(NEXT_JS_STATIC_PATH)`` from ``next.static.scripts``, then bind one ``builder = NextScriptBuilder.from_options(url, NEXT_JS_OPTIONS)``.
   Emit ``builder.preload_link()``, ``builder.script_tag()``, and ``builder.init_script(js_context)`` from a custom template tag or middleware.

Set the policy through the ``NEXT_JS_OPTIONS`` dict.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "NEXT_JS_OPTIONS": {"policy": "disabled"},
   }

Accepted string values for ``policy`` are ``"auto"``, ``"disabled"``, and ``"manual"``.

.. warning::

   When ``policy`` is ``"disabled"``, ``window.Next`` is not defined.
   Any co-located JavaScript or inline script that reads ``window.Next.context`` will fail at runtime.
   Review every ``component.js`` and inline script before switching away from ``AUTO``.

Runtime Script Templates
------------------------

The ``NEXT_JS_OPTIONS`` dict also accepts ``preload_template``, ``script_tag_template``, and ``init_template`` keys.
Each is an HTML string with a single placeholder.
The ``preload_template`` and ``script_tag_template`` use the ``{url}`` placeholder.
The ``init_template`` uses the ``{payload}`` placeholder, which receives the serialized JS context.
Use them to add attributes such as ``nonce``, ``async``, or ``crossorigin`` without writing a custom backend.

.. code-block:: python
   :caption: config/settings.py, adding a crossorigin attribute

   NEXT_FRAMEWORK = {
       "NEXT_JS_OPTIONS": {
           "script_tag_template": '<script src="{url}" crossorigin="anonymous"></script>',
       }
   }

A template carries only its own placeholder, ``{url}`` or ``{payload}``, and no other substitution is supported.
The templates are formatted with Python ``str.format``, not Django templates.
A literal ``{`` or ``}`` inside the template body collides with the formatter and must be doubled to ``{{`` or ``}}`` to survive ``str.format``.
For per-request values such as CSP nonces, use a custom static backend instead.

See Also
--------

.. seealso::

   :doc:`/content/topics/context` for the ``@context`` decorator.
   :doc:`backends` for the ``JS_CONTEXT_POLICY`` option.
   :doc:`/content/howto/override-the-js-context-serializer` for a recipe.
   :doc:`/content/ref/static` for the serializer API.
