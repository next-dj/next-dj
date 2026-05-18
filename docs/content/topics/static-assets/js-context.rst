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

Pass ``serialize=True`` on ``@context`` in ``page.py`` or on ``@component.context`` in ``component.py``.
The value appears under ``window.Next.context.<key>``. Keys without the flag stay server-side only.
Decorator shapes and inheritance rules live in :doc:`../context`.

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

Project Wide Serializer
-----------------------

Set ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` to the dotted path of a serializer class.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
   }

``resolve_serializer`` reads the setting on every call.
When the key is absent the framework uses ``JsonJsContextSerializer``.

Per Key Serializer
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
   Merges nested dicts when both values are dicts.

Configure the policy through the first static backend ``OPTIONS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "JS_CONTEXT_POLICY": "next.static.collector.DeepMergePolicy",
               },
           }
       ]
   }

Reading on the Client
---------------------

Co-located JS and inline scripts read ``window.Next.context``.

.. code-block:: javascript
   :caption: notes/_components/note_card/component.js

   document.addEventListener("DOMContentLoaded", () => {
     const count = window.Next.context.note_count ?? 0;
     console.log(`There are ${count} notes.`);
   });

The runtime script defines ``window.Next`` before the collected scripts run.

Runtime Script Options
----------------------

The setting ``NEXT_FRAMEWORK["NEXT_JS_OPTIONS"]`` is a dict that configures the runtime script builder.
The builder controls how and where the ``Next`` script is injected through a ``ScriptInjectionPolicy``.

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
     - Skips automatic injection but builds the tag strings on request via ``NextScriptBuilder`` methods.
     - Pages where you control placement of the script tags in a layout template.

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

Template Tag Customisation
~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``NEXT_JS_OPTIONS`` dict also accepts ``preload_template``, ``script_tag_template``, and ``init_template`` keys.
Each is an HTML string with a single placeholder.
Use them to add attributes such as ``nonce``, ``async``, or ``crossorigin`` without writing a custom backend.

.. code-block:: python
   :caption: config/settings.py — adding a crossorigin attribute

   NEXT_FRAMEWORK = {
       "NEXT_JS_OPTIONS": {
           "script_tag_template": '<script src="{url}" crossorigin="anonymous"></script>',
       }
   }

The ``{url}`` placeholder is the only supported substitution. The template is formatted with Python ``str.format``, not Django templates.
For per-request values such as CSP nonces, use a custom static backend instead.

See Also
--------

.. seealso::

   :doc:`/content/topics/context` for the ``@context`` decorator.
   :doc:`backends` for the ``JS_CONTEXT_POLICY`` option.
   :doc:`/content/howto/override-the-js-context-serializer` for a recipe.
   :doc:`/content/ref/static` for the serializer API.
