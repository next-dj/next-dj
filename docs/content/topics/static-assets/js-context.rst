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

Pass ``serialize=True`` on the ``@context`` decorator.

.. code-block:: python
   :caption: notes/routes/page.py

   from next.pages import context


   @context("note_count", serialize=True)
   def note_count() -> int:
       return 5

The value lands at ``window.Next.context.note_count`` in the browser.
A context function without ``serialize=True`` stays server side only.

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
The default policy is ``AUTO``, which injects the runtime script before the collected scripts.

Common Patterns
---------------

Hydrate a Component
~~~~~~~~~~~~~~~~~~~

Mark the data a client component needs with ``serialize=True``.
The component reads ``window.Next.context`` without a second request.

Pydantic Models to the Browser
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Set ``JS_CONTEXT_SERIALIZER`` to ``PydanticJsContextSerializer`` so context functions can return Pydantic models directly.

See Also
--------

.. seealso::

   :doc:`/content/topics/context` for the ``@context`` decorator.
   :doc:`backends` for the ``JS_CONTEXT_POLICY`` option.
   :doc:`/content/howto/override-the-js-context-serializer` for a recipe.
   :doc:`/content/ref/static` for the serializer API.
