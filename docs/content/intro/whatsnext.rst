.. _intro-whatsnext:

What to Read Next
=================

The tutorial covers the core flow.
This page points to where each subsystem is documented in depth.

By Subsystem
------------

Routing.
   :doc:`/content/topics/file-router` covers static segments, captured parameters, wildcard segments, and per-project page roots.
   :doc:`/content/topics/url-reversing` covers ``page_reverse`` and ``with_query`` for building URLs in Python.

Pages and layouts.
   :doc:`/content/topics/pages` covers page resolution and template loading.
   :doc:`/content/topics/layouts` covers nested layouts and ordering.

Components.
   :doc:`/content/topics/components` covers simple, composite, and slot-based components.
   :doc:`/content/howto/build-a-composite-component` is a recipe for the most common shape.

Context and dependency injection.
   :doc:`/content/topics/context` covers ``@context`` patterns and serialization.
   :doc:`/content/topics/dependency-injection` covers markers, providers, and the resolver.

Forms and actions.
   :doc:`/content/topics/forms/index` is the entry point for the entire forms surface.
   :doc:`/content/topics/forms/validation-rerender` explains how a failed validation re-renders the origin page.
   :doc:`/content/topics/forms/formsets` covers Django formset support.

Static assets.
   :doc:`/content/topics/static-assets/index` opens the static pipeline guide.
   :doc:`/content/topics/static-assets/asset-kinds` covers built-in kinds and how to add new ones.

Signals.
   :doc:`/content/topics/signals` covers every signal emitted by the framework with payload tables.

Testing.
   :doc:`/content/topics/testing` covers ``NextClient``, ``SignalRecorder``, registry isolation, and helpers.

Extending.
   :doc:`/content/topics/extending` covers the five extension mechanisms used across the framework.

By Task
-------

The :doc:`/content/howto/index` section answers task-shaped questions.
Look there when you have a concrete goal in mind.

For Background
--------------

Internals.
   :doc:`/content/internals/index` walks through how each subsystem works under the hood, with mermaid diagrams.
   Start with :doc:`/content/internals/overview`.

Reference.
   :doc:`/content/ref/index` lists every public module, decorator, signal, and template tag.

Deployment.
   :doc:`/content/deployment/index` covers production settings, ``collectstatic``, and WSGI or ASGI hosting.

Security.
   :doc:`/content/security/index` covers CSRF, XSS, and DI hygiene in the context of next.dj.

Community
---------

Source code lives at ``github.com/next-dj/next-dj``.
File an issue or open a discussion when something is unclear.
Contributions to the documentation are welcome, see :doc:`/content/contributing/index`.
