"""Shared lifecycle for single-use ephemeral sandbox containers.

Used by `DockerEphemeralSandboxExecutor` (untrusted function execution) and
`code_execution` (agent codeExecution). Both run the same `execute_inline`
IPC against the container — only the payload and result shape differ — so only
the container *lifecycle* (resolve baked image → create hardened container →
remove) is shared here.

Hardening mirrors `container_pool._do_create_container`, minus the
unless-stopped restart policy (these containers are single-use). Docker SDK
calls are blocking; callers wrap them via `asyncio.to_thread`. The SDK is
imported lazily so this module is importable in socket-free contexts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_network_ready = False


def _ensure_network(client) -> str:
    import docker

    global _network_ready
    name = settings.sandbox_network
    if _network_ready:
        return name
    try:
        client.networks.get(name)
    except docker.errors.NotFound:
        client.networks.create(name, driver="bridge", labels={"sinas.type": "sandbox"})
    _network_ready = True
    return name


def _run_config(image: str, name: str, network: str) -> dict[str, Any]:
    return {
        "image": image,
        "name": name,
        "detach": True,
        "network": network,
        "mem_limit": f"{settings.max_function_memory}m",
        "nano_cpus": int(settings.max_function_cpu * 1_000_000_000),
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 256,
        "extra_hosts": {"host.docker.internal": "host-gateway"},
        "tmpfs": {"/tmp": "size=100m,mode=1777"},
        "environment": {
            "PYTHONUNBUFFERED": "1",
            "SANDBOX_CONTAINER": "true",
            "SINAS_CONTAINER_MODE": "sandbox",
        },
        "labels": {"sinas.type": "sandbox-executor", "sinas.ephemeral": "true"},
    }


def _create(client, config: dict[str, Any]):
    # Remove a stale container squatting on this name (e.g. a crashed prior run
    # of the same execution_id), then create. Apply the storage limit, falling
    # back if the storage driver doesn't support storage-opt.
    import docker

    try:
        stale = client.containers.get(config["name"])
        stale.remove(force=True)
    except docker.errors.NotFound:
        pass
    try:
        return client.containers.run(
            **config, storage_opt={"size": settings.max_function_storage}
        )
    except Exception as e:
        if "storage-opt" in str(e).lower() or "storage driver" in str(e).lower():
            return client.containers.run(**config)
        raise


async def create_ephemeral_container(
    db: AsyncSession, *, execution_id: str, name_prefix: str = "sinas-sbx"
):
    """Resolve the baked sandbox image, create a hardened single-use container,
    and let its in-container executor daemon settle. Returns (client, container,
    name). The caller is responsible for `remove_ephemeral_container`.
    """
    import docker

    from app.services.sandbox_image import current_sandbox_image

    image = await current_sandbox_image(db)
    client = await asyncio.to_thread(docker.from_env)
    network = await asyncio.to_thread(_ensure_network, client)
    name = f"{name_prefix}-{execution_id}"
    config = _run_config(image, name, network)
    container = await asyncio.to_thread(_create, client, config)
    # Daemon warmup — match the pool's post-create settle.
    await asyncio.sleep(1)
    return client, container, name


async def remove_ephemeral_container(container, name: str) -> None:
    try:
        await asyncio.to_thread(container.remove, force=True)
    except Exception as e:
        logger.warning("Failed to remove ephemeral sandbox %s: %s", name, e)
