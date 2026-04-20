"""Application use cases.

Use cases orchestrate domain aggregates and ports to fulfill a
particular user-facing operation. They are framework-free pure logic —
dependencies flow inward through constructor-injected ports.

Use cases accept a command DTO (validated external input) and return a
result DTO (structured output for the caller to translate to proto,
JSON, etc.).
"""
