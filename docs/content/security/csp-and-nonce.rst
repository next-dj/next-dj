.. _security-csp-and-nonce:

CSP and nonce
=============

A Content Security Policy restricts which scripts a page may run.
The framework writes the request's nonce onto every tag it renders, the client runtime carries the nonce onto every element it inserts, and a patch never runs an inline script, so a nonce-based policy covers the whole page.
This page covers where the nonce comes from, what carries it, why scripts in patches never run, and the interplay with a cached page.

.. contents::
   :local:
   :depth: 1

Where the nonce comes from
--------------------------

``NEXT_FRAMEWORK["CSP_NONCE"]`` is a bool, ``True`` by default, and ``False`` turns nonces off.
While it is on, the framework reads ``request.csp_nonce`` from django-csp and falls back to ``get_nonce`` of Django's own CSP middleware, which Django 6.0 added and Django 5.2 lacks.
Reading the nonce is what makes either middleware mint it, so the header they send names the same value.
The nonce is read once per request, and without either middleware installed no tag carries one.

.. code-block:: python
   :caption: config/settings.py, with django-csp

   from csp.constants import NONCE, NONE, STRICT_DYNAMIC

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "csp.middleware.CSPMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
   ]

   CONTENT_SECURITY_POLICY = {
       "DIRECTIVES": {
           "script-src": [STRICT_DYNAMIC, NONCE],
           "object-src": [NONE],
           "base-uri": [NONE],
       },
   }

What carries it
---------------

Every tag the framework writes carries ``nonce="..."``.

- The ``next.min.js`` tag, its preload hint, and the inline ``Next._init`` payload, through the ``{nonce_attr}`` placeholder of the ``NEXT_JS_OPTIONS`` templates.
- Every collected stylesheet, script, and module tag, through the ``nonce`` keyword the injector passes to each backend renderer and the ``{nonce_attr}`` placeholder of the ``css_tag``, ``js_tag``, and ``module_tag`` templates.
- The inline ``{% #use_script %}`` and ``{% #use_style %}`` blocks.
- The head scripts of ``scripts.py`` and the manifest entries the runtime inserts later, see :doc:`/content/topics/scripts/declaring`.

A custom template without ``{nonce_attr}`` renders a tag the policy refuses while a nonce is active, and ``manage.py check`` names the setting that holds it.

The runtime carries it on
-------------------------

The runtime remembers the nonce of the script that bootstrapped it, read from ``document.currentScript.nonce``, and copies it onto every element it injects for a co-located asset delta, a script, a module, a ``<link rel="stylesheet">``, and an inline ``<style>`` alike, and onto the scripts chunk and, under ``DEBUG``, the dev chunk it fetches.
The nonce is the only attribute the runtime carries over from the page.
An element it builds for a patch-inserted asset takes a fixed attribute set, so an ``integrity`` or ``crossorigin`` attribute a backend writes into its tag templates reaches the browser on a full render alone, see :doc:`/content/topics/partial-rendering/limitations`.

Scripts in patches never run
----------------------------

A ``<script>`` inside patch HTML is never executed by any insertion path.
The applier removes every script element from parsed patch HTML before it reaches the document.
The sweep reaches into the content of every ``<template>`` in the patch as well, nested ones included, because a ``{% #consented %}`` block keeps its body in a template and revealing it would otherwise run a script the patch carried.
This is structural neutralisation, not a parser side effect, so there is no element for the browser to evaluate and no nonce question to answer.

Behaviour arrives only through the co-located asset manifest, whose scripts are nonced, and through the ``event`` verb, which carries no code.
A widget that relied on an inline initialiser in its markup moves to a co-located module, see :doc:`/content/topics/partial-rendering/co-located-js`.
With the runtime's dev mode on, that is with Django ``DEBUG``, the runtime prints a ``console.warn`` for every script it neutralises.

What the applier does not remove
--------------------------------

The removal covers ``<script>`` elements and nothing else.
An event-handler attribute such as ``onclick``, an ``<iframe srcdoc>`` carrying a document of its own, and a ``javascript:`` href all reach the live document exactly as the server wrote them, because each one is an attribute rather than an element the applier can cut out.

A project running under a nonce policy without ``'unsafe-inline'`` already has its defence, since the browser refuses all three.
A project running without a CSP has one defence left, the template's own auto escaping, which is what keeps an untrusted value from becoming an attribute in the first place.
Never build patch HTML by string concatenation around a request value, and never ``mark_safe`` a value the request supplied, see :doc:`di-and-untrusted-input`.

Cached pages
------------

A nonce is meant to be unique per response, and a page a CDN caches would repeat the one nonce its copy was rendered with for every visitor who receives that copy.
The framework therefore treats a minted nonce as one visitor's, and a render that carries one goes out ``private`` even when its ``cache`` lets a shared cache keep it.
While a nonce is active, ``manage.py check`` warns about every page whose ``cache`` a CDN may hold, since none of them reaches the edge.
A site that caches pages on a CDN sets ``CSP_NONCE`` to ``False`` and admits its scripts by hash or by source instead, see :doc:`/content/howto/cache-pages-on-a-cdn`.

strict-dynamic as a recommendation
----------------------------------

``'strict-dynamic'`` lets a script already trusted by a nonce load further scripts without each one needing its own nonce in the policy.
It pairs well with the runtime, because the nonced bootstrap loads the asset scripts and the vendor loaders insert their own, and ``'strict-dynamic'`` propagates the trust to them.
The framework carries the nonce and refuses to run inline patch scripts, and everything past that is your policy to author and verify against your own threat model.

See also
--------

.. seealso::

   :doc:`/content/topics/scripts/declaring` for the third-party scripts the nonce covers.
   :doc:`/content/security/static-assets` for the origin and integrity of shipped assets.
   :doc:`/content/ref/settings` for ``CSP_NONCE``.
