"""C6 frontend view-model builder."""

from importlib import import_module
from typing import Any

__all__ = ["DecisionValidationError", "build", "build_viewmodels", "validate_decision"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module(".build", __name__), name)
