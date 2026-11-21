"""Errors every language-model provider raises.

Kept apart from app/model.py, which imports the providers, so the providers can
import these without a circular import.
"""


class ModelError(Exception):
    """The model could not be reached or gave nothing usable."""


class ModelUnavailable(ModelError):
    """The model is not configured (no provider chosen, or its credentials missing)."""
