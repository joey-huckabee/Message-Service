"""Full-service fixtures for e2e tests.

Planned fixtures:

* ``running_service`` — starts the service in-process with all ports bound
  to ephemeral addresses, yields a handle with port numbers, teardown
  issues graceful shutdown and waits for completion.
* ``grpc_stub`` — a ``message_service_pb2_grpc.MessageServiceStub`` bound
  to ``running_service``'s gRPC port.
* ``dashboard_client`` — authenticated ``httpx.AsyncClient`` bound to
  ``running_service``'s dashboard port; pre-seeds a test user.
* ``authenticated_admin_client`` — same but as an admin user.
"""

from __future__ import annotations

# TODO(L3-DEP-010, L3-DEP-011): implement once the service entry point
# supports in-process construction.
