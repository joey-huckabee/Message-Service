"""Service composition root — builds every adapter and use case.

The bootstrap module is the ONLY place in the codebase where concrete
adapters meet concrete use cases. Everywhere else depends on ports;
the bootstrap injects the implementations.

Public surface:

* :func:`build_service` — construct a :class:`Service` from a loaded
  :class:`Config`. Async because it needs to open the SQLite connection
  and apply migrations.
* :func:`shutdown_service` — teardown pair for :func:`build_service`.
* :class:`Service` — frozen carrier of the composed service state.

Usage::

    from message_service.config.loader import load_config
    from message_service.bootstrap import build_service, shutdown_service

    config = load_config("/etc/message-service/config.toml")
    service = await build_service(config)
    try:
        # hand ``service`` to the gRPC / HTTP servers
        ...
    finally:
        await shutdown_service(service, timeout=config.service.shutdown_grace_period_seconds)
"""

from message_service.bootstrap.service import (
    Service,
    build_service,
    shutdown_service,
)

__all__ = ["Service", "build_service", "shutdown_service"]
