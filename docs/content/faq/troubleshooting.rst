.. _faq-troubleshooting:

Troubleshooting
===============

This page lists the most common errors and warnings plus the actions that resolve them.

.. contents::
   :local:
   :depth: 2

Pages
-----

Page does not appear at the expected URL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the directory contains a ``page.py`` plus a ``template.djx`` (or any other body source).
Confirm that the application is listed in ``INSTALLED_APPS`` and ``APP_DIRS=True`` in the page backend.

Run ``uv run python manage.py check`` and resolve every warning.

Page renders without layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A layout must contain ``{% block template %}{% endblock template %}``.
Without the placeholder the framework drops the body silently.

Confirm that ``layout.djx`` sits in an ancestor directory.

next.W043 warning
~~~~~~~~~~~~~~~~~

A page module declares more than one body source.
Pick one of ``render``, ``template`` attribute, or sibling ``template.djx``.
Remove the others.

Forms
-----

HTTP 400 from form submission
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The dispatcher rejected the request because ``_next_form_page`` is missing or invalid.
Always render the form through ``{% form @action="name" %}`` or include both ``csrf_token`` and the ``_next_form_page`` field by hand.

HTTP 403 on POST
~~~~~~~~~~~~~~~~

CSRF token is missing or stale.
The ``{% form %}`` tag injects the token automatically.
Manual forms need ``{% csrf_token %}`` plus a fresh cookie.

next.E040 collision
~~~~~~~~~~~~~~~~~~~

Two actions share the same UID.
Add a ``namespace=`` prefix to one of them or rename one.

next.E041 missing form_class
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A handler that declares a ``form`` parameter must register with ``form_class=``.
Add the form class to the decorator.

Components
----------

next.E020 or next.E034 collision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two components share the same name in the same scope.
Rename one or move one to a different page tree.

Component does not render
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that ``COMPONENTS_DIR`` is set on ``DEFAULT_COMPONENT_BACKENDS``.
Confirm that the component folder name matches the string argument to ``{% component %}``.

Component prop does not resolve
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``{% component "card" title=some_var %}`` resolves ``some_var`` from the parent template context.
``{% component "card" title="some_var" %}`` is a literal string.
Pick the form that matches the value you want to pass.

Static
------

CSS or JS not loaded
~~~~~~~~~~~~~~~~~~~~

Confirm that ``{% collect_styles %}`` sits in the layout ``<head>`` and ``{% collect_scripts %}`` sits at the bottom of ``<body>``.
Confirm that the asset filename matches a registered stem and a registered kind.

Hashed URL does not change
~~~~~~~~~~~~~~~~~~~~~~~~~~

Restart the development server.
The watcher picks up file content changes but a hash computed at startup can stale during long sessions.

Dependency Injection
--------------------

DI parameter resolves to None
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The annotation might be a string (under ``from __future__ import annotations``).
Remove the future import in page modules, layout modules, component modules, and provider modules.

Custom marker not handled
~~~~~~~~~~~~~~~~~~~~~~~~~

Confirm that the provider class is imported during ``AppConfig.ready``.
``RegisteredParameterProvider`` registers at class creation, so the import must happen before the resolver caches the provider list.

URL Resolution
--------------

URL name not found
~~~~~~~~~~~~~~~~~~

Run ``uv run python manage.py shell`` and print ``reverse("next:page_<name>")``.
Confirm that the file router walked the relevant directory.

Routes do not refresh
~~~~~~~~~~~~~~~~~~~~~

For a custom router backend that reads from the database call ``router_manager.reload()`` after data changes.
See :doc:`/content/howto/reload-routes-from-code`.

System Checks
-------------

Run system checks
~~~~~~~~~~~~~~~~~

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

The output lists every check that fired with a code and a hint.
Most errors point at the configuration key that needs to change.

See Also
--------

.. seealso::

   :doc:`/content/ref/system-checks` for the full check catalog.
   :doc:`/content/topics/index` for in depth guides.
