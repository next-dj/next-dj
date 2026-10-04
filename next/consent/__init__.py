"""The consent a visitor gives per category, read on the server and sent to the runtime.

The framework provides the mechanism, not compliance, and denies every category but
necessary by default.
"""

from . import checks, providers, signals
from .backends import ConsentBackend, CookieConsentBackend
from .manager import consent_categories, get_consent
from .markers import NECESSARY, UNDECIDED, Consent


__all__ = [
    "NECESSARY",
    "UNDECIDED",
    "Consent",
    "ConsentBackend",
    "CookieConsentBackend",
    "checks",
    "consent_categories",
    "get_consent",
    "signals",
]
