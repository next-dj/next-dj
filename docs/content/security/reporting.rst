.. _security-reporting:

Reporting a vulnerability
=========================

This page covers how to report a security vulnerability in next.dj.
The disclosure process is private and the maintainers acknowledge every report.
The canonical policy is :repo:`SECURITY.md <blob/main/SECURITY.md>` in the repository, and this page restates it for the manual.

.. contents::
   :local:
   :depth: 2

Where to report
---------------

Use the private :repo:`Security advisories <security/advisories>` form on the repository and click *Report a vulnerability*.
The form accepts an encrypted description and lets the maintainers coordinate a patch before public disclosure.

Public issues and pull requests are not the right channel.
A public report exposes users before a fix is available.

What to include
---------------

A complete report contains the following.

- Dependency versions that match your environment (for example a lock file or the output of ``pip freeze``) so maintainers can reproduce the stack.
- The affected subsystem (pages, components, forms, urls, partial, static, deps, server, conf).
- A reproducible test case or at minimum a step by step description.
- The observed impact, including any account or data exposure.
- Suggested mitigations if you have any.

A reproducible test case shortens the triage time considerably.

What happens next
-----------------

The maintainers acknowledge receipt within a few business days and follow up with an initial assessment.
A fix is prepared and released once the assessment confirms the issue.
A coordinated public disclosure happens after the fix is available.

Out of scope
------------

Third-party applications built with next.dj are out of scope unless the flaw is in next.dj itself.

See also
--------

.. seealso::

   :doc:`/content/contributing/index` for the broader contribution process.
   :doc:`overview` for the broader security picture.
