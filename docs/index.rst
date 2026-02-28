:layout: landing
:description: next.dj — A full-stack Django framework with file-based routing, DJX templates, and modern web development patterns.
:content_max_width: 100%

Full-stack Django framework
==============================

.. rst-class:: lead

next.dj brings **file-based routing** and **modern frontend patterns** to Django. You get a Next.js-like structure—pages, layouts, and forms from the filesystem—while keeping Django's backend, ORM, and ecosystem. Less boilerplate, clear conventions, one stack.

.. container:: buttons

   :doc:`Getting started <content/guide/getting-started>`
   `Source code <https://github.com/next-dj/next-dj>`_

Features
--------

.. grid:: 1 1 2 3
   :gutter: 2
   :padding: 0
   :class-row: surface

   .. grid-item-card:: :octicon:`file-directory` File-based routing
      :link: content/guide/file-router
      :link-type: doc

      Stop editing ``urls.py`` for every new page. Add a file, get a route—ship faster and forget routing boilerplate.

   .. grid-item-card:: :octicon:`browser` Pages and templates
      :link: content/guide/pages-and-templates
      :link-type: doc

      One layout, many pages. Reuse structure instead of copy-pasting—less repetition and fewer bugs as you scale.

   .. grid-item-card:: :octicon:`database` Context and data
      :link: content/guide/context
      :link-type: doc

      No more passing the same data in every view. Define once, use everywhere—views stay clean and easy to change.

   .. grid-item-card:: :octicon:`pencil` Forms and actions
      :link: content/guide/forms
      :link-type: doc

      Forms that render and validate with less code. Handlers get request, form, and params automatically—no manual wiring.

   .. grid-item-card:: :octicon:`plug` Dependency injection
      :link: content/guide/dependency-injection
      :link-type: doc

      Get request and params where you need them, without threading through functions. Less glue code, faster iteration.

   .. grid-item-card:: :octicon:`sync` Development server
      :link: content/guide/autoreload
      :link-type: doc

      Edit and see. No restart, no manual refresh—focus on building instead of waiting for the server.

Open source
-----------

.. admonition:: Open source
   :class: note

   next.dj is **open source**. Contributions—bug fixes, docs, and ideas—are welcome. See :doc:`content/contributing/contributing` to get started.

.. toctree::
   :caption: Guide
   :hidden:
   :maxdepth: 1

   content/guide/getting-started
   content/guide/file-router
   content/guide/pages-and-templates
   content/guide/context
   content/guide/forms
   content/guide/dependency-injection
   content/guide/autoreload

.. toctree::
   :caption: API Reference
   :hidden:
   :maxdepth: 1

   content/api/reference

.. toctree::
   :caption: Contributing
   :hidden:
   :maxdepth: 1

   content/contributing/contributing
   content/contributing/documentation-guide
