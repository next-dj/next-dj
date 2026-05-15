.. _howto-share-context:

Share Context Across Pages
==========================

Problem
-------

You want to publish a value once and read it from every page below a particular layout.

Solution
--------

Declare the value in the ``layout.py`` that sits at the root of the subtree and pass ``inherit_context=True`` to the decorator.

Walkthrough
-----------

Create the layout module.

.. code-block:: python
   :caption: notes/routes/layout.py

   from notes.models import Note

   from next.pages import context


   @context("note_count", inherit_context=True)
   def note_count() -> int:
       return Note.objects.count()

Use the value from any page below the layout.

.. code-block:: jinja
   :caption: notes/routes/layout.djx

   <header>
     There are {{ note_count }} notes.
   </header>
   {% block template %}{% endblock template %}

The value is also available in any descendant page, not only in the layout.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   <p>{{ note_count }} notes in total.</p>

Limit Inheritance to a Subtree
------------------------------

Drop the flag for values that should remain local to the layout.

.. code-block:: python
   :caption: per layout only

   from next.pages import context


   @context("nav_links")
   def nav_links() -> list:
       return [...]

Without ``inherit_context=True`` the value reaches only the layout template, not the descendant pages.

Override From a Page
--------------------

A descendant page that declares the same key overrides the inherited value for itself.

.. code-block:: python
   :caption: notes/routes/notes/[id]/page.py

   from next.pages import context

   from notes.models import Note


   @context("note_count")
   def specific_count() -> int:
       return Note.objects.filter(featured=True).count()

The override only applies to the page that declares it.
The layout still sees the original value when it renders.

Verification
------------

Visit two pages under the layout and confirm the value renders on both.
Visit a page outside the layout and confirm the value is undefined.

See Also
--------

.. seealso::

   :doc:`/content/topics/context` for ``inherit_context`` rules.
   :doc:`/content/topics/layouts` for the layout chain.
