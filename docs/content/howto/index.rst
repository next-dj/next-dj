.. _howto:

How-To Guides
=============

How-to guides answer task-shaped questions.
Each recipe states a problem, gives one minimal solution, walks through the steps, and shows how to verify the result.
Topic guides cover the underlying concepts, see :doc:`/content/topics/index`.

.. rubric:: Pages and routing

:doc:`add-a-page`
   Add a new page to a running project.

:doc:`reverse-urls`
   Build URLs from page templates and Python.

:doc:`reload-routes-from-code`
   Trigger router reload at runtime.

:doc:`write-a-router-backend`
   Implement a router backend that reads URLs from a database.

:doc:`read-query-parameters`
   Read query-string values with the ``DQuery`` marker.

.. rubric:: Context and components

:doc:`share-context-across-pages`
   Publish values once for an entire page tree.

:doc:`resolve-feature-flags-with-di`
   Resolve feature flags through a custom dependency provider.

:doc:`build-a-composite-component`
   Build a component that takes content through slots.

:doc:`share-components-across-projects`
   Reuse a UI kit across several page roots.

.. rubric:: Static assets

:doc:`add-a-new-asset-kind`
   Recognise a new file extension during static collection.

:doc:`add-a-custom-stem`
   Treat additional filenames as component assets.

:doc:`write-a-static-backend`
   Customise the rendered link and script tags.

:doc:`override-the-js-context-serializer`
   Replace the default JSON serializer for the ``Next`` object.

:doc:`build-a-custom-asset-backend`
   Add a custom asset kind with its own renderer.

.. rubric:: Forms

:doc:`handle-file-uploads`
   Accept file inputs in a form action.

:doc:`use-modelform-for-crud`
   Power create, update, and delete pages with ``ModelForm``.

:doc:`use-formsets`
   Render and validate a Django formset.

:doc:`write-a-form-action-backend`
   Plug a new validation pipeline into the dispatcher.

:doc:`extend-a-default-backend`
   Patch one key of a default backend entry through ``extend_default_backend``.

.. rubric:: Testing and integration

:doc:`test-a-page-with-actions`
   Test a page that posts to a registered action.

:doc:`integrate-django-admin`
   Run Django admin alongside next.dj pages.

:doc:`observe-framework-signals`
   Receive framework signals in production receivers.

:doc:`scope-requests-per-tenant`
   Scope every request to one tenant across the stack.

:doc:`stream-live-updates-with-sse`
   Push live updates to the browser with server-sent events.

.. toctree::
   :hidden:
   :maxdepth: 1

   add-a-page
   reverse-urls
   reload-routes-from-code
   write-a-router-backend
   read-query-parameters
   share-context-across-pages
   build-a-composite-component
   share-components-across-projects
   resolve-feature-flags-with-di
   add-a-new-asset-kind
   add-a-custom-stem
   write-a-static-backend
   override-the-js-context-serializer
   build-a-custom-asset-backend
   handle-file-uploads
   use-modelform-for-crud
   use-formsets
   write-a-form-action-backend
   extend-a-default-backend
   test-a-page-with-actions
   integrate-django-admin
   observe-framework-signals
   scope-requests-per-tenant
   stream-live-updates-with-sse
