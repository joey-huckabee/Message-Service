# Message-Service — ROADMAP

This document records items that have been explicitly deferred from the v1 scope during requirements elicitation. Each item has a rationale for deferral and, where applicable, a trigger that would prompt reconsideration.

Items in this file are **not** requirements. When an item is promoted into a future release, it is moved out of this file and into `docs/L1-REQ.md` with a fresh requirement identifier.

## Testing and verification

- **Test strategy document** — a top-level document covering unit test conventions, integration test harness for gRPC and FastAPI, end-to-end run-simulation fixtures, orphan-path test harness, and SMTP sandbox configuration.
- **pytest marker auto-extraction tool** — `scripts/build-trace-matrix.py` to scan `@pytest.mark.requirement("L<N>-<CAT>-<NNN>")` markers across the test suite and auto-populate the Verification Artifacts column of `docs/TRACE-MATRIX.md`. Eliminates manual matrix maintenance.
- **Coverage enforcement** — CI gate requiring every approved L1 requirement to have at least one linked verification artifact before release.

## Performance and profiling

- **In-flight run state backing profiling** — v1 co-locates in-flight run state in SQLite, relying on SQLite's built-in WAL journal for durability. If profiling later shows SQLite write latency is a bottleneck on the gRPC ingest hot path, evaluate an in-memory store with a custom write-ahead log. The repository-pattern abstraction (L1-PERS-003) makes this swap possible without touching domain code.
- **Email size distribution analysis** — once the Prometheus email-size histogram has collected production data, analyze for patterns that would justify per-pipeline-type size limits or automatic compression strategies.

## Security hardening

- **Mutual TLS on gRPC** — v1 uses plaintext TCP on the trusted ISOLAN network. Promote when gRPC ingest crosses trust boundaries or when compliance requirements demand transport encryption.
- **Additional authentication backends** — LDAP/AD and OIDC. Current scope is local accounts only. LDAP integration is the likely first addition, consistent with broader ISOLAN architecture patterns.
- **Secrets handling review** — SMTP credentials and any future API keys currently live in the TOML configuration file. Consider integration with Vault CE if secret rotation becomes operationally significant.

## Feature extensions

- **Per-pipeline-type orphan policy override** — v1 applies a single global orphan disposition policy. Future work allows per-pipeline overrides of the policy set, with the global policy as fallback.
- **Hot-reload of tag vocabulary** — v1 loads the tag configuration at service start. Hot-reload removes the need for restart to add tags.
- **Hot-reload of templates** — v1 ships templates with the code and loads them at service start. Hot-reload would allow operators to roll out new template versions without restart.
- **Subscription granularity extensions** — beyond `GLOBAL`, `PIPELINE`, `TAG`: consider per-severity, per-submitter, or boolean combinations of existing granularities if use cases emerge.
- **Alternative delivery transports** — v1 delivers via SMTP. Future options include webhooks, direct API hooks into ticketing systems, and Slack/Teams relays.
- **Streaming gRPC RPCs** — v1 uses unary RPCs only. If live run-progress streaming becomes a pipeline-side need, add a server-streaming `WatchRun` endpoint.
- **Custom WAL for in-flight state** — dependent on the profiling item above. Would replace the SQLite-backed in-flight state with an in-memory representation plus an append-only log file.

## Operations

- **High availability and multi-node** — v1 is single-node. Multi-node introduces leader election, shared state, and coordinated orphan sweeping; substantial scope.
- **Air-gapped installer bundle** — a single-archive offline installer for ISOLAN deployment that bundles the Poetry-locked dependency tree, NSSM on Windows, and systemd unit on Linux.
- **Backup and restore tooling** — scripts to snapshot and restore the SQLite database and rendered-reports directory as an atomic unit.
- **Audit log archival** — once retention expires, archive rather than delete, to satisfy long-term investigative needs.
- **Metrics dashboard templates** — ship pre-built Grafana dashboards in addition to the embedded in-service visualizations.

## Documentation

- **Architecture decision records (ADRs)** — capture the rationale for significant architectural choices as standalone records in `docs/adr/`, supplementing the Rationale field on individual requirements.
- **Operator runbook** — failure modes, diagnostic procedures, recovery steps for common incidents (SMTP relay down, SQLite corruption, runaway orphan sweeper).
- **Template author guide** — how to add a new template to the manifest, define its JSON Schema, and test it in isolation.
- **Pipeline integration guide** — example code and sequence diagrams for pipeline authors consuming the `message-service-proto` definitions.
