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

:doc:`add-a-custom-template-loader`
   Plug an extra ``TemplateLoader`` into ``TEMPLATE_LOADERS``.

:doc:`require-login-on-pages`
   Gate routed pages behind authentication.

:doc:`internationalize-routes`
   Serve the page tree under per-language URL prefixes.

:doc:`customize-error-pages`
   Replace the default 404 and 500 pages.

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

:doc:`build-a-multi-step-wizard`
   Gather data across several steps and finalise one row at the end.

:doc:`move-from-django-formtools`
   Port a formtools ``WizardView`` flow to ``FormWizard``.

:doc:`style-forms-with-crispy-and-widget-tweaks`
   Render forms through crispy-forms and widget-tweaks inside ``{% form %}``.

:doc:`drive-form-actions-with-htmx`
   Submit forms over htmx and swap only the form region.

:doc:`integrate-django-allauth-forms`
   Drive the allauth login, signup, and reset forms through form actions.

:doc:`write-a-form-action-backend`
   Plug a new validation pipeline into the dispatcher.

:doc:`extend-a-default-backend`
   Patch one key of a default backend entry through ``extend_default_backend``.

.. rubric:: Testing

:doc:`test-a-page-with-actions`
   Test a page that posts to a registered action.

:doc:`test-a-component-in-isolation`
   Render and assert on one component without a page.

.. rubric:: Integration and operations

:doc:`integrate-django-admin`
   Run Django admin alongside next.dj pages.

:doc:`scope-requests-per-tenant`
   Scope every request to one tenant across the stack.

:doc:`observe-framework-signals`
   Receive framework signals in production receivers.

:doc:`stream-live-updates-with-sse`
   Push live updates to the browser with server-sent events.

:doc:`split-settings-per-environment`
   Split settings into base, dev, and prod modules.

.. toctree::
   :hidden:
   :maxdepth: 1

   add-a-page
   reverse-urls
   reload-routes-from-code
   write-a-router-backend
   read-query-parameters
   add-a-custom-template-loader
   require-login-on-pages
   internationalize-routes
   customize-error-pages
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
   build-a-multi-step-wizard
   move-from-django-formtools
   style-forms-with-crispy-and-widget-tweaks
   drive-form-actions-with-htmx
   integrate-django-allauth-forms
   write-a-form-action-backend
   extend-a-default-backend
   test-a-page-with-actions
   test-a-component-in-isolation
   integrate-django-admin
   scope-requests-per-tenant
   observe-framework-signals
   stream-live-updates-with-sse
   split-settings-per-environment
