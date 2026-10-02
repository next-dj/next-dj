"""The consent a visitor gives per category, read on the server and sent to the runtime.

The framework offers the mechanism, not compliance, denying every category by default.
"""

from . import checks, providers, signals
from .backends import ConsentBackend, CookieConsentBackend
from .manager import consent_categories, get_consent
from .markers import NECESSARY, UNDECIDED, Consent, joint_category


__all__ = [
    "NECESSARY",
    "UNDECIDED",
    "Consent",
    "ConsentBackend",
    "CookieConsentBackend",
    "checks",
    "consent_categories",
    "get_consent",
    "joint_category",
    "signals",
]
