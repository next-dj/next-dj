.. _howto-js-serializer:

Override the JS Context Serializer
==================================

Problem
-------

The default JSON serializer does not know how to encode Pydantic models or other custom types that you publish through ``@context(serialize=True)``.

Solution
--------

Point ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` at a serializer class, or pass ``serializer=`` on a single ``@context`` decorator.

Walkthrough
-----------

Use the Bundled Pydantic Serializer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The framework ships ``PydanticJsContextSerializer``.
Point the setting at it.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
   }

Context functions can now return Pydantic models directly.

.. code-block:: python
   :caption: notes/routes/page.py

   from pydantic import BaseModel

   from next.pages import context


   class NoteOut(BaseModel):
       id: int
       title: str


   @context("featured", serialize=True)
   def featured() -> NoteOut:
       return NoteOut(id=1, title="Hello")

Write a Custom Serializer
~~~~~~~~~~~~~~~~~~~~~~~~~

A serializer is any class with a ``dumps`` method that returns a JSON string.

.. code-block:: python
   :caption: notes/serializers.py

   import json

   from django.core.serializers.json import DjangoJSONEncoder


   class SortedSerializer:
       """Serialise context values with sorted keys for stable output."""

       def dumps(self, value: object) -> str:
           return json.dumps(
               value,
               cls=DjangoJSONEncoder,
               separators=(",", ":"),
               sort_keys=True,
           )

Point the setting at the class.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "notes.serializers.SortedSerializer",
   }

Per Key Override
~~~~~~~~~~~~~~~~

Pass ``serializer=`` on a single ``@context`` to route one key through a custom serializer.

.. code-block:: python
   :caption: per key

   from next.pages import context
   from next.static import PydanticJsContextSerializer


   @context("note", serialize=True, serializer=PydanticJsContextSerializer())
   def note() -> object:
       return load_note()

The override applies only to the ``note`` key.
Every other key uses the project serializer.

Verification
------------

Reload a page and inspect ``window.Next.context`` in the browser console.
The previously failing values now appear as proper JSON.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/js-context` for the topic guide.
   :doc:`/content/topics/context` for the ``serialize`` flag.
