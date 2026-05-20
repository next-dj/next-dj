.. _security-reporting:

Reporting a Vulnerability
=========================

This page covers how to report a security vulnerability in next.dj.
The disclosure process is private and the maintainers acknowledge every report.

.. contents::
   :local:
   :depth: 2

Where to Report
---------------

Use the private GitHub Security Advisory form at the project repository.
The form accepts an encrypted description and lets the maintainers coordinate a patch before public disclosure.

Public issues and pull requests are not the right channel.
A public report exposes users before a fix is available.

What to Include
---------------

A complete report contains the following.

- Dependency versions that match your environment (for example a lock file or the output of ``pip freeze``) so maintainers can reproduce the stack.
- The affected subsystem (pages, components, forms, static, deps, server, conf).
- A reproducible test case or at minimum a step by step description.
- The observed impact, including any account or data exposure.
- Suggested mitigations if you have any.

A reproducible test case shortens the triage time considerably.

What Happens Next
-----------------

The maintainers respond within five business days with an acknowledgement and an initial assessment.
A fix is prepared and released once the assessment confirms the issue.
A coordinated public disclosure happens after the fix is available.

Reporter Credit
---------------

The fix announcement credits the reporter unless the reporter prefers to remain anonymous.

Out of Scope
------------

The following items are out of scope for the security advisory program.

- Vulnerabilities in projects built on top of next.dj that are caused by user code.
- Issues that require a malicious local administrator account.
- Self denial of service through an extremely large form payload.
- Discoveries that depend on a fork or modified copy of the framework.

See Also
--------

.. seealso::

   :doc:`/content/contributing/index` for the broader contribution process.
   :doc:`overview` for the broader security picture.
