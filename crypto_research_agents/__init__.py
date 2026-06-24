"""Backward-compatible import alias for the renamed :mod:`jimmoria` package."""

from __future__ import annotations

from importlib import import_module

_jimmoria = import_module("jimmoria")

APP_NAME = _jimmoria.APP_NAME
__version__ = _jimmoria.__version__
__all__ = getattr(_jimmoria, "__all__", ["APP_NAME", "__version__"])

# Let imports such as ``crypto_research_agents.cli`` resolve to modules under
# the primary ``jimmoria`` package path while old integrations migrate.
__path__ = _jimmoria.__path__
