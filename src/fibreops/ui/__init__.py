"""FibreOps Operations Console — lightweight web UI for the BRK241 demo.

Run with ``python -m fibreops.demo ui`` (or ``uvicorn fibreops.ui.app:app``).

We deliberately do NOT re-export ``app`` here to avoid shadowing the
``fibreops.ui.app`` submodule (Python would bind the FastAPI instance to the
submodule name in the package namespace, breaking ``import fibreops.ui.app``).
Use ``from fibreops.ui.app import app`` or ``uvicorn fibreops.ui.app:app``.
"""
from .app import create_app

__all__ = ["create_app"]
