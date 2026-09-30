"""Single source of truth for the Sinas platform version.

This module intentionally contains nothing but the version literal so that:

1. `pyproject.toml` can read it via setuptools dynamic metadata
   (`[tool.setuptools.dynamic] version = {attr = "app._version.__version__"}`)
   without importing the rest of the `app` package.

2. The backend `Dockerfile` can copy *only this file* (plus `pyproject.toml`
   and `__init__.py`) into the early build layer to resolve the version,
   keeping the dep-install layer cache valid across code edits. The cache
   layer rebuilds only when this file changes — i.e., on a version bump,
   which is exactly when you want it to rebuild.

Bump the version here and nowhere else. The runtime (`app/main.py`,
`/info` endpoint) imports `__version__` from `app/__init__.py`, which
re-exports this value.
"""

__version__ = "0.4.0"
