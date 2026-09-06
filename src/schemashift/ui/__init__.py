"""Gradio presentation layer for SchemaShift.

The import is intentionally lazy so ``python -m schemashift.ui.app`` does not
pre-import the module that :mod:`runpy` is about to execute.
"""

from __future__ import annotations

from typing import Any


def build_app(*args: Any, **kwargs: Any) -> Any:
    """Build the Gradio application without eagerly importing Gradio."""
    from schemashift.ui.app import build_app as _build_app

    return _build_app(*args, **kwargs)


__all__ = ["build_app"]
