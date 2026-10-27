"""System health endpoint — single source of truth for the admin dashboard."""
import asyncio
import logging
import shutil
import time
from typing import Any

import docker
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import require_permission
from app.core.config import settings
from app.schemas.system import ContainerRestartResponse, HealthResponse
from app.services import k8s_stats
from app.services.k8s_stats import deployment_mode
from app.services.queue_service import queue_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/system", tags=["system"])

# Known compose service prefixes to look for
COMPOSE_SERVICES = [
    "backend",
    "queue-worker",
    "queue-agent",
    "scheduler",
    "redis",
    "postgres",
    "pgbouncer",
    "clickhouse",
    "console",
    "caddy",
    "cdc-worker",
]


def _parse_cpu_percent(stats: dict) -> float:
    """Calculate CPU usage percentage from Docker stats."""
    try:
        cpu = stats["cpu_stats"]
        precpu = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - precpu["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - precpu["system_cpu_usage"]
        if system_delta > 0 and cpu_delta >= 0:
            num_cpus = cpu["online_cpus"]
            return round((cpu_delta / system_delta) * num_cpus * 100, 1)
    except (KeyError, ZeroDivisionError, TypeError):
        pass
    return 0.0


def _parse_memory(stats: dict) -> dict:
    """Parse memory usage from Docker stats."""
    try:
        usage = stats["memory_stats"]["usage"]
        limit = stats["memory_stats"]["limit"]
        # Subtract cache for more accurate reading
        cache = stats["memory_stats"].get("stats", {}).get("cache", 0)
        actual = usage - cache
        return {
            "used_mb": round(actual / 1024 / 1024, 1),
            "limit_mb": round(limit / 1024 / 1024, 1),
            "percent": round((actual / limit) * 100, 1) if limit > 0 else 0,
        }
    except (KeyError, TypeError):
        return {"used_mb": 0, "limit_mb": 0, "percent": 0}


def _get_container_info(container, include_stats: bool = False) -> dict:
    """Extract info from a Docker container object (blocking — run in thread)."""
    name = container.name
    attrs = container.attrs
    state = attrs.get("State", {})
    status = container.status

    # Health check status
    health = state.get("Health", {}).get("Status", "none")

    # Uptime
    started_at = state.get("StartedAt", "")
    uptime_seconds = 0
    if started_at and status == "running":
        try:
            from datetime import datetime, timezone

            # Docker uses ISO format with nanoseconds
            clean = started_at.split(".")[0] + "Z"
            started = datetime.fromisoformat(clean.replace("Z", "+00:00"))
            uptime_seconds = int((datetime.now(timezone.utc) - started).total_seconds())
        except Exception:
            pass

    result = {
        "name": name,
        "status": status,
        "health": health,
        "uptime_seconds": uptime_seconds,
    }

    # Get CPU/memory stats (expensive — ~1-2s per container)
    if include_stats and status == "running":
        try:
            stats = container.stats(stream=False)
            result["cpu_percent"] = _parse_cpu_percent(stats)
            result["memory"] = _parse_memory(stats)
        except Exception:
            result["cpu_percent"] = 0
            result["memory"] = {"used_mb": 0, "limit_mb": 0, "percent": 0}

    return result


def _get_host_resources() -> dict:
    """Get host CPU, memory, and disk usage."""
    result = {
        "cpu_percent": None,
        "memory_total_mb": None,
        "memory_used_mb": None,
        "memory_percent": None,
        "disk_total_gb": None,
        "disk_used_gb": None,
        "disk_percent": None,
    }

    # Disk usage (works everywhere)
    try:
        usage = shutil.disk_usage("/")
        result["disk_total_gb"] = round(usage.total / 1024 / 1024 / 1024, 1)
        result["disk_used_gb"] = round(usage.used / 1024 / 1024 / 1024, 1)
        result["disk_percent"] = round((usage.used / usage.total) * 100, 1)
    except Exception:
        pass

    # Memory from /proc/meminfo (Linux / Docker container — reflects host)
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]  # value in kB
                    meminfo[key] = int(val)

            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)
            used = total - available
            result["memory_total_mb"] = round(total / 1024, 1)
            result["memory_used_mb"] = round(used / 1024, 1)
            result["memory_percent"] = round((used / total) * 100, 1) if total > 0 else 0
    except Exception:
        pass

    # CPU from /proc/stat (Linux — two readings 0.5s apart)
    try:
        def read_cpu():
            with open("/proc/stat") as f:
                line = f.readline()
                parts = line.split()
                # user, nice, system, idle, iowait, irq, softirq, steal
                values = [int(x) for x in parts[1:9]]
                idle = values[3] + values[4]  # idle + iowait
                total = sum(values)
                return idle, total

        idle1, total1 = read_cpu()
        time.sleep(0.1)
        idle2, total2 = read_cpu()

        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        if total_delta > 0:
            result["cpu_percent"] = round((1.0 - idle_delta / total_delta) * 100, 1)
    except Exception:
        pass

    return result


async def _redis_memory() -> dict[str, Any] | None:
    """Redis memory against its maxmemory, its eviction policy, and what it
    evicted or refused since it started. None when Redis can't be read.

    INFO is allowed on managed Redis (Memorystore, ElastiCache), where
    CONFIG GET is not; errorstats needs Redis 6.2 (absent: no OOM count)."""
    from app.core.redis import get_redis

    try:
        redis = await get_redis()
        memory = await redis.info("memory")
        stats = await redis.info("stats")
    except Exception as e:
        logger.warning(f"Could not read Redis memory info: {e}")
        return None
    try:
        errorstats = await redis.info("errorstats")
    except Exception:
        errorstats = {}

    used = int(memory.get("used_memory") or 0)
    maxmemory = int(memory.get("maxmemory") or 0)  # 0 = no limit
    oom = errorstats.get("errorstat_OOM")
    return {
        "used_bytes": used,
        "maxmemory_bytes": maxmemory or None,
        "percent": round(used / maxmemory * 100, 1) if maxmemory else None,
        "policy": memory.get("maxmemory_policy"),
        "evicted_keys": int(stats.get("evicted_keys") or 0),
        "oom_errors": int(oom.get("count", 0)) if isinstance(oom, dict) else None,
    }


def _redis_warnings(redis_memory: dict[str, Any] | None) -> list[dict]:
    """At maxmemory a noeviction Redis refuses writes (requests and jobs
    fail with OOM errors); any other policy silently drops state."""
    if not redis_memory:
        return []
    warnings = []
    percent = redis_memory.get("percent")
    policy = redis_memory.get("policy")
    if percent is not None and percent >= 75:
        consequence = (
            "writes fail when it is full" if policy == "noeviction"
            else "it evicts keys when full"
        )
        warnings.append({
            "level": "critical" if percent >= 90 else "warning",
            "message": f"Redis memory at {percent}% of maxmemory ({consequence})",
        })
    if redis_memory.get("oom_errors"):
        warnings.append({
            "level": "critical",
            "message": (
                f"Redis refused {redis_memory['oom_errors']} write(s) for lack of memory "
                "since it started; raise its maxmemory"
            ),
        })
    if policy and policy != "noeviction":
        warnings.append({
            "level": "warning",
            "message": (
                f"Redis maxmemory-policy is {policy}: under memory pressure it can drop "
                "queued jobs, locks and the dead-letter queue. Use noeviction."
            ),
        })
    if redis_memory.get("evicted_keys"):
        warnings.append({
            "level": "warning",
            "message": f"Redis has evicted {redis_memory['evicted_keys']} key(s) since it started",
        })
    return warnings


def _generate_warnings(
    services: list[dict],
    queue_workers: list[dict],
    dlq_size: int,
    queue_stats: dict,
    host: dict,
    redis_memory: dict[str, Any] | None = None,
) -> list[dict]:
    """Generate health warnings from system state."""
    warnings = _redis_warnings(redis_memory)

    # Critical: no queue workers
    fn_workers = [w for w in queue_workers if w.get("queue") == "functions"]
    agent_workers = [w for w in queue_workers if w.get("queue") == "agents"]

    if not fn_workers:
        warnings.append({"level": "critical", "message": "No function queue workers running"})
    if not agent_workers:
        warnings.append({"level": "critical", "message": "No agent queue workers running"})

    # Critical: infrastructure services down
    infra_services = {"redis", "postgres", "pgbouncer"}
    for svc in services:
        # Extract service name from container name
        svc_name = svc.get("service", "")
        if svc_name in infra_services and svc["status"] != "running":
            warnings.append({
                "level": "critical",
                "message": f"{svc_name} is {svc['status']}",
            })

    # Warning: any compose service not running
    for svc in services:
        svc_name = svc.get("service", "")
        if svc_name not in infra_services and svc["status"] != "running":
            warnings.append({
                "level": "warning",
                "message": f"{svc_name} ({svc['name']}) is {svc['status']}",
            })

    # Warning: unhealthy containers
    for svc in services:
        if svc.get("health") == "unhealthy":
            warnings.append({
                "level": "warning",
                "message": f"{svc['name']} is unhealthy",
            })

    # Warning: DLQ
    if dlq_size > 0:
        warnings.append({
            "level": "warning",
            "message": f"Dead letter queue has {dlq_size} item{'s' if dlq_size != 1 else ''}",
        })
    dlq_dropped = queue_stats.get("dlq", {}).get("dropped", 0)
    if dlq_dropped:
        warnings.append({
            "level": "warning",
            "message": (
                f"{dlq_dropped} dead-lettered job(s) were dropped: the dead letter queue "
                f"is capped at {queue_stats['dlq'].get('max_entries')} (QUEUE_DLQ_MAX_ENTRIES)"
            ),
        })

    # Warning: queue backlog
    for q_name in ["functions", "agents"]:
        pending = queue_stats.get("queues", {}).get(q_name, {}).get("pending", 0)
        if pending > 50:
            warnings.append({
                "level": "warning",
                "message": f"{q_name} queue has {pending} pending jobs",
            })

    # Host resource warnings
    disk_pct = host.get("disk_percent")
    mem_pct = host.get("memory_percent")

    if disk_pct is not None:
        if disk_pct > 90:
            warnings.append({"level": "warning", "message": f"Disk usage at {disk_pct}%"})
        elif disk_pct > 75:
            warnings.append({"level": "info", "message": f"Disk usage at {disk_pct}%"})

    if mem_pct is not None:
        if mem_pct > 90:
            warnings.append({"level": "warning", "message": f"Memory usage at {mem_pct}%"})
        elif mem_pct > 75:
            warnings.append({"level": "info", "message": f"Memory usage at {mem_pct}%"})

    return warnings


async def _docker_services(include_stats: bool) -> tuple[list[dict], str | None]:
    """Sinas containers from the Docker socket. Returns (services, unavailable_reason)."""
    try:
        client = docker.from_env()

        # Get all sinas compose containers from Docker (filter by name prefix)
        all_containers = await asyncio.to_thread(
            client.containers.list,
            all=True,
            filters={"name": "sinas"},
        )
    except Exception as e:
        logger.warning(f"Failed to query Docker for health check: {e}")
        return [], (
            f"Docker API not reachable from the backend ({type(e).__name__}); "
            "service status needs the Docker socket mounted into this container."
        )

    compose_containers = []
    for c in all_containers:
        # Skip the executor image-builder container (exits immediately by design)
        if c.name.startswith("sinas-executor"):
            continue
        labels = c.labels or {}
        service = labels.get("com.docker.compose.service", c.name)
        # Sandbox/shared containers inherit service=executor from the image,
        # use a more descriptive service name instead
        if c.name.startswith("sinas-sandbox-"):
            service = "sandbox"
        elif c.name.startswith("sinas-shared-"):
            service = "shared"
        compose_containers.append((c, service))

    # Get container info in parallel
    async def get_info(container, service_name):
        info = await asyncio.to_thread(_get_container_info, container, include_stats)
        info["service"] = service_name
        return info

    services = await asyncio.gather(
        *[get_info(c, svc) for c, svc in compose_containers],
        return_exceptions=True,
    )
    services = [s for s in services if isinstance(s, dict)]

    # Sort: critical infra first, then alphabetical
    priority = {"redis": 0, "postgres": 1, "pgbouncer": 2, "clickhouse": 3, "backend": 4}
    services.sort(key=lambda s: (priority.get(s["service"], 99), s["name"]))
    return services, None


@router.get("/health")
async def get_system_health(
    include_stats: bool = Query(False, description="Include per-container CPU/memory stats (slow)"),
    user_id: str = Depends(require_permission("sinas.system.read:all")),
) -> HealthResponse:
    """Comprehensive system health check.

    Services come from the Docker socket on docker deployments and from the
    Kubernetes API (release pods + metrics.k8s.io) on Kubernetes. A source
    that can't be read degrades to an `*_unavailable_reason` instead of a 500.
    """
    deployment = deployment_mode()
    services: list[dict] = []
    services_reason: str | None = None
    metrics_reason: str | None = None
    try:
        if deployment == "kubernetes":
            services, services_reason, metrics_reason = await k8s_stats.list_services(
                include_stats
            )
        else:
            services, services_reason = await _docker_services(include_stats)
    except Exception as e:
        logger.warning(f"Failed to list services for health check: {e}")
        services_reason = f"Service status unavailable ({type(e).__name__})."

    # Get host resources (blocking /proc reads, run in thread)
    try:
        host = await asyncio.to_thread(_get_host_resources)
    except Exception as e:
        logger.warning(f"Failed to get host resources for health check: {e}")
        host = {
            "cpu_percent": None,
            "memory_total_mb": None,
            "memory_used_mb": None,
            "memory_percent": None,
            "disk_total_gb": None,
            "disk_used_gb": None,
            "disk_percent": None,
        }

    # Get queue worker info and DLQ size for warnings
    queue_workers = []
    dlq_size = 0
    queue_stats = {}
    try:
        queue_workers = await queue_service.get_active_workers()
        queue_stats = await queue_service.get_queue_stats()
        dlq_size = queue_stats.get("dlq", {}).get("size", 0)
    except Exception as e:
        logger.warning(f"Failed to get queue stats for health check: {e}")

    redis_memory = await _redis_memory()

    warnings = _generate_warnings(
        services, queue_workers, dlq_size, queue_stats, host, redis_memory
    )

    return HealthResponse(
        warnings=warnings,
        services=services,
        host=host,
        redis=redis_memory,
        deployment=deployment,
        executors={
            "sandbox": settings.sandbox_executor,
            "trusted": settings.trusted_executor,
        },
        services_unavailable_reason=services_reason,
        metrics_unavailable_reason=metrics_reason,
    )


@router.post("/containers/{container_name}/restart")
async def restart_container(
    container_name: str,
    user_id: str = Depends(require_permission("sinas.system.update:all")),
) -> dict[str, str]:
    """Restart a Docker container by name (docker deployments only)."""
    if deployment_mode() == "kubernetes":
        raise HTTPException(
            status_code=409,
            detail="Restarting from the console is only available on Docker deployments; "
            "on Kubernetes use `kubectl rollout restart` (or delete the pod).",
        )
    try:
        client = docker.from_env()
        container = await asyncio.to_thread(client.containers.get, container_name)
    except docker.errors.NotFound:
        raise HTTPException(
            status_code=404, detail=f"Container '{container_name}' not found"
        ) from None
    except docker.errors.DockerException as e:
        raise HTTPException(
            status_code=503, detail=f"Docker API not reachable from the backend ({type(e).__name__})"
        ) from e

    await asyncio.to_thread(container.restart, timeout=15)
    return ContainerRestartResponse(status="restarted", container=container_name)


@router.post("/flush-stuck-jobs")
async def flush_stuck_jobs(
    user_id: str = Depends(require_permission("sinas.system.update:all")),
) -> dict[str, Any]:
    """Cancel jobs stuck in 'running' state for over 2 hours."""
    result = await queue_service.flush_stuck_jobs()
    return result
