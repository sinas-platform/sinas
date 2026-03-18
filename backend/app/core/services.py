"""Service registry for singleton instances.

Provides a central place to access and swap service singletons.
This enables testing (swap with mocks) and avoids circular imports
(lazy initialization on first access).

Usage in production code:
    from app.core.services import services
    services.queue_service.enqueue(...)

Usage in tests:
    services.override("queue_service", mock_queue)
    services.reset()  # restore originals
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Lazy-initializing, swappable service registry."""

    def __init__(self):
        self._instances: dict[str, Any] = {}
        self._overrides: dict[str, Any] = {}
        self._factories: dict[str, callable] = {}

    def register(self, name: str, factory: callable) -> None:
        """Register a factory for lazy initialization."""
        self._factories[name] = factory

    def override(self, name: str, instance: Any) -> None:
        """Override a service instance (for testing)."""
        self._overrides[name] = instance

    def reset(self) -> None:
        """Clear all overrides and cached instances."""
        self._overrides.clear()
        self._instances.clear()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        # Check overrides first
        if name in self._overrides:
            return self._overrides[name]

        # Return cached instance
        if name in self._instances:
            return self._instances[name]

        # Lazy-initialize from factory
        if name in self._factories:
            self._instances[name] = self._factories[name]()
            return self._instances[name]

        raise AttributeError(f"Service '{name}' not registered")


services = ServiceRegistry()

# Register service factories (lazy — only created on first access)
services.register("queue_service", lambda: _import("app.services.queue_service", "QueueService")())
services.register("executor", lambda: _import("app.services.execution_engine", "FunctionExecutor")())
services.register("stream_relay", lambda: _import("app.services.stream_relay", "StreamRelay")())
services.register("encryption_service", lambda: _import("app.core.encryption", "EncryptionService")())
services.register("clickhouse_logger", lambda: _import("app.services.clickhouse_logger", "ClickHouseLogger")())
services.register("template_service", lambda: _import("app.services.template_service", "TemplateService")())
services.register("container_pool", lambda: _import("app.services.container_pool", "ContainerPool")())
services.register("shared_worker_manager", lambda: _import("app.services.shared_worker_manager", "SharedWorkerManager")())
services.register("scheduler", lambda: _import("app.services.scheduler", "FunctionScheduler")())


def _import(module_path: str, class_name: str):
    """Import a class by module path — avoids circular imports."""
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)
