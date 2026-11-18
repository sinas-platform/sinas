# uv installs the dependencies from uv.lock (exact versions + hashes). Pinned
# by tag and digest; it is only mounted for the install step, not shipped.
FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (needed for psycopg2 and other packages)
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy only the files needed to resolve the package metadata and install deps.
# uv.lock pins every dependency (transitive included) to an exact version and
# hash, so a rebuild installs the same set as the last one instead of whatever
# satisfies pyproject's ranges that day. pyproject.toml uses setuptools dynamic version (`attr = "app._version.__version__"`),
# so setuptools must be able to locate the `app` package and read `_version.py`
# at install time. We copy the three minimal files that make that possible:
#   - pyproject.toml         — declares the dynamic version attr
#   - app/__init__.py        — marks `app` as a regular package (required for find)
#   - app/_version.py        — the literal that the attr resolves to
# Code edits to the rest of the app DO NOT invalidate this layer. A version
# bump touches _version.py, which intentionally rebuilds the dep layer —
# that's the cost of changing what gets installed, and it's bounded to once
# per release rather than once per commit.
COPY pyproject.toml uv.lock /app/
COPY app/__init__.py /app/app/__init__.py
COPY app/_version.py /app/app/_version.py

# Install the locked dependencies, plus this project in editable mode, into the
# image's system Python (/usr/local), not a .venv: the dev compose bind-mounts
# ./backend over /app, which would hide a venv living there.
#   --locked   fail the build if uv.lock is stale against pyproject.toml
#   --no-dev   runtime deps only (the `dev` extra is not installed)
#   --inexact  keep pip/setuptools that ship with the base image
# uv verifies every downloaded artifact against the hashes in uv.lock.
ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --inexact --no-cache

# Copy the rest of the application
COPY . /app/

# Copy executor script for user containers
COPY container_executor.py /app/executor.py

# Non-root runtime user: uid 10001, primary group 0. Arbitrary-UID friendly
# (OpenShift-style): every path the app writes to is owned by group 0 and
# group-writable, so the image also runs under a platform-assigned uid as
# long as its group is 0. Writable paths:
#   /app/logs          log directory
#   /var/sinas/files   file storage (a volume in production; the image copy
#                      seeds the ownership of a fresh Docker named volume)
#   /home/sinas        $HOME, for libraries that write config/cache there
# /tmp is world-writable already. Python bytecode is not written at runtime
# (PYTHONDONTWRITEBYTECODE=1) and dependencies live in the system Python, so
# the application code and site-packages stay read-only to this user.
RUN useradd --uid 10001 --gid 0 --no-create-home --home-dir /home/sinas \
        --shell /usr/sbin/nologin sinas \
    && mkdir -p /app/logs /var/sinas/files /home/sinas \
    && chgrp -R 0 /app/logs /var/sinas/files /home/sinas \
    && chmod -R g=u /app/logs /var/sinas/files /home/sinas
ENV HOME=/home/sinas
USER 10001:0

# Expose the port the app runs on
EXPOSE 8000

# Run database migrations on startup, then start the application with multiple workers
# UVICORN_WORKERS environment variable can override default of 4
# --proxy-headers: the client IP (IP rate limits, request logs) is taken from
# X-Forwarded-For, but only on connections from FORWARDED_ALLOW_IPS (read by
# uvicorn; default 127.0.0.1). Set it to your reverse proxy's address(es) as
# the container sees them; never "*" when port 8000 is reachable directly.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers ${UVICORN_WORKERS:-4}