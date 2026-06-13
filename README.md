# next.dj

[![PyPI version](https://img.shields.io/pypi/v/next.dj)](https://pypi.python.org/pypi/next.dj/)
[![PyPI Supported Python Versions](https://img.shields.io/pypi/pyversions/next.dj.svg)](https://pypi.python.org/pypi/next.dj/)
[![PyPI Supported Django Versions](https://img.shields.io/pypi/djversions/next.dj.svg)](https://pypi.python.org/pypi/next.dj/)
[![codecov](https://codecov.io/gh/next-dj/next-dj/graph/badge.svg?token=6RY9344W4E)](https://codecov.io/gh/next-dj/next-dj)

A next-gen framework based on Django without the tears.

> [!WARNING]
> This project is under active development. Treat releases as evolving until you validate behaviour for your workload.

## What is `next.dj`?

`next.dj` adds file-based routing, nested `layout.djx` wrappers, reusable components with co-located assets, dependency-injected context and actions, forms that register themselves next to pages with declarative access guards, a multi-step `FormWizard`, and form dispatch via `{% form %}` and `{% action_url %}`. Directories map to URLs. A `page.py` file turns a segment into a page. Configuration lives in the `NEXT_FRAMEWORK` mapping alongside standard Django settings.

## Documentation

Full documentation is available at https://next-dj.readthedocs.io/.

## Contributing

We welcome contributions from the community! `next.dj` is designed to make Django development more accessible to frontend developers, and your input is invaluable.

## Sponsors

<a target="_blank" href="https://evrone.com">
    <img src="./docs/_static/img/evrone_sponsored.svg" width="130px" alt="Sponsored by Evrone" />
</a>

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
