.. _topics-static-js-context:

JavaScript Context
==================

next.dj ships a ``Next`` object to the browser that holds page context, URL kwargs, and a few framework keys.
This page covers how to opt context values in, how the wire format is built, and how to swap the serializer that converts Python values into JavaScript.

.. contents::
   :local:
   :depth: 2

The Next Object
---------------

The framework injects one ``<script>`` per page that defines ``window.Next`` before any other ``{% collect_scripts %}`` output.
The object has three top level fields.

``context``.
   The map of context keys that opted into serialization.

``url_kwargs``.
   The captured URL parameters for the current page.

``page``.
   Metadata about the current page including its URL name and the absolute path of its ``page.py``.

Opting In
---------

Pass ``serialize=True`` on the ``@context`` decorator.

.. code-block:: python
   :caption: notes/routes/page.py

   from next.pages import context


   @context("note_count", serialize=True)
   def note_count() -> int:
       return 5

The value lands at ``Next.context.note_count`` in the browser.
Functions without ``serialize=True`` stay on the server side only.

Per Key Serializer
------------------

Override the serializer for a single key through the ``serializer`` argument.

.. code-block:: python
   :caption: per key serializer

   from pydantic import BaseModel

   from next.pages import context


   class NoteOut(BaseModel):
       id: int
       title: str


   def to_dict(value: NoteOut) -> dict:
       return value.model_dump()


   @context("featured", serialize=True, serializer=to_dict)
   def featured() -> NoteOut:
       return NoteOut(id=1, title="Hello")

The framework calls the function before JSON encoding.
Use this for Pydantic models, dataclasses, NumPy arrays, or any other type that JSON cannot encode directly.

Project Wide Serializer
-----------------------

Set ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` to a dotted path that points at a callable.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "notes.serializers.json_context",
   }

The callable receives a dict and returns a dict.
The framework JSON encodes the result.

.. code-block:: python
   :caption: notes/serializers.py

   from datetime import datetime


   def json_context(payload: dict) -> dict:
       result = {}
       for key, value in payload.items():
           if isinstance(value, datetime):
               result[key] = value.isoformat()
           else:
               result[key] = value
       return result

The project wide serializer runs before per key serializers.
Use it to apply universal conversions and let per key overrides handle specialised types.

Wire Format
-----------

The framework emits a script of this shape.

.. code-block:: html
   :caption: rendered output

   <script id="next-context" type="application/json">
   {"context": {"note_count": 5}, "url_kwargs": {"id": 7}, "page": {"name": "next:page_notes_id", "module": "/abs/path/to/page.py"}}
   </script>
   <script>
   window.Next = JSON.parse(document.getElementById("next-context").textContent);
   </script>

Two scripts run.
The first holds the JSON payload.
The second parses it and assigns to ``window.Next``.

Reading on the Client
---------------------

Any inline script or co-located JS can read ``Next``.

.. code-block:: javascript
   :caption: notes/_components/note_card/component.js

   document.addEventListener("DOMContentLoaded", () => {
     const count = Next.context.note_count ?? 0;
     console.log(`There are ${count} notes.`);
   });

The framework injects ``Next`` before ``{% collect_scripts %}`` emits the co-located scripts so the variable is always defined when component scripts run.

Key Conflict
------------

A page that registers two context functions with the same key is a configuration error.
The framework reports the conflict through ``next.E090`` at startup with the file location of both functions.

Excluding Keys
--------------

Pass ``serialize=False`` to explicitly opt a value out of serialization when a global serializer would otherwise pick it up.

.. code-block:: python
   :caption: explicit opt out

   from next.pages import context


   @context("secret_token", serialize=False)
   def token() -> str:
       return load_secret_token()

The value reaches the template but not the browser.

JSContextSerializer Class
-------------------------

For more elaborate transformations use the ``next.static.serializers.JsContextSerializer`` base class.

.. code-block:: python
   :caption: subclass

   from next.static.serializers import JsContextSerializer


   class JsonContextSerializer(JsContextSerializer):
       def serialize_value(self, key: str, value: object) -> object:
           if key.startswith("secret_"):
               return None
           return super().serialize_value(key, value)

Point ``JS_CONTEXT_SERIALIZER`` at the dotted path of the subclass.
The framework instantiates it once per process.

Browser Side Patterns
---------------------

Hydrating a Component
~~~~~~~~~~~~~~~~~~~~~

Use ``Next.context`` to hydrate a React or Vue component without a separate XHR.

Page Specific Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``Next.url_kwargs`` to read captured URL parameters from the client.

Feature Flags
~~~~~~~~~~~~~

Pass feature flag values through ``Next.context`` and gate client side features on them.

See Also
--------

.. seealso::

   :doc:`/content/topics/context` for ``@context`` and ``serialize`` flags.
   :doc:`/content/howto/override-the-js-context-serializer` for a recipe.
   :doc:`/content/ref/static` for the serializer API.
