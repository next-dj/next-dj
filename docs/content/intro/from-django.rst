.. _intro-from-django:

Coming from Django
==================

next.dj keeps Django underneath and replaces the wiring layer above it.
This page maps the Django concepts a reader already knows onto their next.dj counterparts.
Each row links to the page that owns the detail, so this page stays a lookup table rather than a second explanation.

.. contents::
   :local:
   :depth: 2

What next.dj replaces
---------------------

The left column names the Django idiom, the middle column names the shape that takes its place, and the right column links to the page that documents the mechanism in full.

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Django
     - next.dj
     - Detail
   * - A ``path()`` entry in ``urls.py`` for every view
     - A directory under a page root, with one ``include("next.urls")`` in the root URLconf
     - :doc:`/content/topics/file-router`
   * - A view function or a class-based view
     - A ``page.py`` module beside a ``template.djx``
     - :doc:`/content/topics/pages`
   * - ``TemplateView`` with no logic of its own
     - A directory holding only a ``template.djx``, which the router serves as a virtual route
     - :doc:`/content/topics/file-router`
   * - ``get_context_data``
     - ``@context("name")`` callables declared in ``page.py``
     - :doc:`/content/topics/context`
   * - ``self.kwargs["note_id"]``
     - A ``[int:note_id]`` directory segment read through ``DUrl[int]``
     - :doc:`/content/topics/file-router`
   * - ``request.GET.get("page")``
     - A parameter annotated ``DQuery[int]``
     - :doc:`/content/topics/dependency-injection`
   * - ``FormView`` with its URL entry, its template, and its redirect
     - A ``Form`` or ``ModelForm`` subclass that registers itself and gains a POST endpoint
     - :doc:`/content/topics/forms/overview`
   * - ``form_valid``
     - ``on_valid``
     - :doc:`/content/topics/forms/actions`
   * - ``success_url``
     - ``Meta.success_url``
     - :doc:`/content/topics/forms/actions`
   * - ``LoginRequiredMixin`` and ``PermissionRequiredMixin``
     - ``Meta.login_required`` and ``Meta.permission_required``
     - :ref:`topics-forms-actions-guards`
   * - ``{% extends "base.html" %}``
     - An ancestor ``layout.djx`` that wraps every page below its directory
     - :doc:`/content/topics/layouts`
   * - ``{% include "_card.html" %}``
     - ``{% component "card" %}``, a folder carrying its own template, Python, CSS, and JS
     - :doc:`/content/topics/components`
   * - ``reverse("notes:detail", args=[note.id])``
     - ``page_reverse("notes/[id]", id=note.id)``, with the generated ``next:page_...`` name still available to ``reverse``
     - :doc:`/content/topics/url-reversing`
   * - ``django.test.Client``
     - ``NextClient``, a subclass that adds action and zone helpers
     - :doc:`/content/topics/testing`
   * - A full page reload after every POST
     - A ``{% zone %}`` block the server re-renders on its own
     - :doc:`/content/topics/partial-rendering/index`

The table maps shapes, not a mechanical rewrite.
A ``FormView`` carries a URL, a view class, and a redirect target, and the next.dj replacement folds all three into one class declaration.
:doc:`/content/topics/forms/overview` has a class-based-view mapping of its own that covers the remaining hooks.

What next.dj extends
--------------------

Two Django subsystems keep working exactly as before and gain an extra source.

Template context processors.
   The processors listed in the first ``TEMPLATES`` entry run for file-routed pages as well.
   Each page backend may add more under its ``OPTIONS.context_processors`` key, and the framework merges both lists with duplicates dropped.
   See :doc:`/content/topics/context` for the merge order.

Static files.
   ``django.contrib.staticfiles``, ``STATICFILES_DIRS``, ``{% static %}``, and ``collectstatic`` behave as in any Django project.
   next.dj registers an extra finder that also serves the CSS and JS files sitting next to a page or a component, and injects their tags into the ``{% collect_styles %}`` and ``{% collect_scripts %}`` slots.
   See :doc:`/content/topics/static-assets/overview`.

.. _intro-from-django-unchanged:

What stays the same
-------------------

next.dj adds no middleware and no model layer, so the following stay Django's.

- Models, the ORM, and migrations are untouched.
- ``django.contrib.admin`` mounts as usual, see :doc:`/content/howto/integrate-django-admin`.
- Authentication, permissions, and sessions come from ``django.contrib.auth``.
- The ``MIDDLEWARE`` list is yours, and the framework contributes no entry to it.
- Management commands, caching, signals, and internationalisation follow Django's documentation.
- Ordinary ``.html`` templates rendered by hand-written views keep rendering.
- Plain Django views coexist with file-routed pages, see *Coexisting with plain Django views* in :doc:`/content/topics/pages`.

Where to start
--------------

Install the package first, then build the Notes application to see each replacement in a running project.
:doc:`install` covers the settings block and the single ``include`` line.
:doc:`tutorial01` starts the six-part walkthrough.

.. seealso::

   :doc:`overview` for the mental model behind the file router, layouts, and actions.
   :doc:`/content/faq/usage` for short answers to the questions that follow a port.
   :doc:`/content/misc/glossary` for the nouns this documentation uses.
