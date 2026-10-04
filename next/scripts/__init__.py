"""Third-party scripts a page tree declares in `scripts.py`, gated by consent.

The server writes the allowed head scripts, and the runtime loads the rest.
"""

from . import checks, signals
from .errors import ScriptsSourceImportError
from .markers import Script, Strategy


__all__ = ["Script", "ScriptsSourceImportError", "Strategy", "checks", "signals"]
