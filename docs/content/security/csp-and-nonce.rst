.. _security-csp-and-nonce:

CSP and Nonce
=============

A Content Security Policy restricts which scripts a page may run.
The partial runtime is designed to live under one, and this page covers how the runtime carries a nonce, why scripts in patches never run, and what ``'strict-dynamic'`` does and does not guarantee.

.. contents::
   :local:
   :depth: 1

The Nonce From currentScript
----------------------------

The runtime remembers the nonce of the script that bootstrapped it.
On load it reads ``document.currentScript.nonce`` and copies that value onto every script element it injects for a co-located asset delta.
A dynamically inserted ``<script>`` carries the page nonce, so a policy that allows nonced scripts allows the asset the runtime loads.

The bootstrap script tag already carries the nonce your CSP middleware stamps on it, so there is nothing extra to configure on the runtime side.
The asset scripts inherit it.

Scripts in Patches Never Run
----------------------------

A ``<script>`` inside patch HTML is never executed by any insertion path.
The applier removes every script element from parsed patch HTML before it reaches the document.
This is structural neutralisation, not a parser side effect: the script is cut out, so there is no element for the browser to evaluate and no nonce question to answer.

The consequence for a CSP is that a morph cannot smuggle an inline script past the policy, because a morph cannot run an inline script at all.
Behaviour arrives only through the co-located asset manifest, whose scripts are nonced, and through the ``event`` verb, which carries no code.
A widget that relied on an inline initialiser in its markup has to move to a co-located module, see :doc:`/content/topics/partial-rendering/co-located-js`.

In a development build the runtime prints a ``console.warn`` for every script it neutralises, so an inline initialiser that stopped working is visible rather than silent.

strict-dynamic as a Recommendation
-----------------------------------

``'strict-dynamic'`` lets a script already trusted by a nonce load further scripts without each one needing its own nonce in the policy.
It pairs well with the runtime, because the nonced bootstrap script loads the asset scripts and ``'strict-dynamic'`` propagates that trust to them.

This is a recommendation, not a guarantee the framework can make for your deployment.
A CSP is your policy.
The framework carries the nonce and refuses to run inline patch scripts, which removes the two mechanisms a partial update could otherwise use to bypass a policy.
It does not author your policy, validate your directives, or promise that any particular policy is correct for your site.
Treat ``'strict-dynamic'`` as a sensible default for a nonce-based policy and verify the resulting headers against your own threat model.

A Worked Policy
---------------

A nonce-based policy with ``'strict-dynamic'`` looks like this, with ``{nonce}`` filled in per request by your CSP middleware.

.. code-block:: text
   :caption: a Content-Security-Policy header

   Content-Security-Policy: script-src 'nonce-{nonce}' 'strict-dynamic'; object-src 'none'; base-uri 'none'

The bootstrap script tag carries ``nonce="{nonce}"``, the runtime copies that nonce onto every asset script it injects, and ``'strict-dynamic'`` lets the bootstrap load them.
No patch can introduce an inline script, so no patch needs a nonce of its own.

See Also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/co-located-js` for moving inline initialisers to co-located modules.
   :doc:`/content/security/static-assets` for the origin and integrity of shipped assets.
   :doc:`/content/topics/partial-rendering/sse` for the streaming surface.
