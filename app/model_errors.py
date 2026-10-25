"""Errors every model provider raises, so callers never depend on a provider."""


class ModelError(Exception):
    """The model could not be reached or gave nothing usable."""


class ModelUnavailable(ModelError):
    """The model is not configured (no provider chosen, or its settings missing)."""
