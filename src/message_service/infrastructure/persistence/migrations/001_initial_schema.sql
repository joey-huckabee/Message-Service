-- Initial schema for message-service.
--
-- One file per migration; this one creates every table. Subsequent
-- migrations (002_*, 003_*, ...) evolve the schema.
--
-- Conventions:
-- * All timestamps stored as ISO-8601 UTC strings with explicit "Z"
--   suffix (L3-RUN-025). SQLite has no native datetime; TEXT is
--   correct.
-- * JSON columns stored as TEXT. Application code serializes with
--   json.dumps(sort_keys=True, separators=(",", ":")) for
--   deterministic round-trip.
-- * State enums stored as their StrEnum value (TEXT). CHECK
--   constraints enforce the valid set at the DB level too.
-- * Foreign keys enabled via PRAGMA foreign_keys=ON at connect time
--   (L3-PERS-002).

-- ---------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------
CREATE TABLE users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0,   -- L3-SUB-017; 0 or 1
    created_at    TEXT NOT NULL,
    CHECK (disabled IN (0, 1))
);

-- ---------------------------------------------------------------------
-- runs
-- ---------------------------------------------------------------------
CREATE TABLE runs (
    run_id                      TEXT PRIMARY KEY,         -- canonical UUID string
    pipeline_type               TEXT NOT NULL,
    state                       TEXT NOT NULL,
    attachment_mode             TEXT NOT NULL,
    aggregation_template_name   TEXT,                      -- NULL when PER_STAGE
    aggregation_template_version TEXT,                     -- NULL when PER_STAGE
    tags_json                   TEXT NOT NULL,             -- sorted JSON array
    declared_stages_json        TEXT NOT NULL,             -- JSON array of objects
    subscription_predicate_tags_json TEXT NOT NULL,        -- sorted JSON array
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    CHECK (state IN (
        'INITIATED', 'AGGREGATING', 'READY', 'SENDING',
        'SENT', 'FAILED', 'ORPHANED'
    )),
    CHECK (attachment_mode IN ('SINGLE_AGGREGATED', 'PER_STAGE')),
    CHECK (
        (attachment_mode = 'PER_STAGE'
         AND aggregation_template_name IS NULL
         AND aggregation_template_version IS NULL)
        OR
        (attachment_mode = 'SINGLE_AGGREGATED'
         AND aggregation_template_name IS NOT NULL
         AND aggregation_template_version IS NOT NULL)
    )
);

CREATE INDEX idx_runs_state         ON runs(state);
CREATE INDEX idx_runs_pipeline_type ON runs(pipeline_type);
CREATE INDEX idx_runs_updated_at    ON runs(updated_at);

-- ---------------------------------------------------------------------
-- stages
-- ---------------------------------------------------------------------
CREATE TABLE stages (
    run_id                        TEXT NOT NULL,
    stage_id                      TEXT NOT NULL,
    state                         TEXT NOT NULL,
    report_template_name          TEXT NOT NULL,
    report_template_version       TEXT NOT NULL,
    report_context_json           TEXT,                -- NULL permitted
    email_body_context_json       TEXT,                -- NULL permitted
    submitted_at                  TEXT,                -- NULL when PENDING
    PRIMARY KEY (run_id, stage_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    CHECK (state IN (
        'PENDING', 'SUBMITTED', 'ACCEPTED', 'RETRIED',
        'TIMEOUT', 'FAILED'
    )),
    CHECK (
        (state = 'PENDING' AND submitted_at IS NULL)
        OR
        (state != 'PENDING' AND submitted_at IS NOT NULL)
    )
);

-- ---------------------------------------------------------------------
-- subscriptions
-- ---------------------------------------------------------------------
CREATE TABLE subscriptions (
    subscription_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    granularity      TEXT NOT NULL,
    target_value     TEXT,                      -- NULL iff GLOBAL
    created_at       TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CHECK (granularity IN ('GLOBAL', 'PIPELINE', 'TAG')),
    CHECK (
        (granularity = 'GLOBAL' AND target_value IS NULL)
        OR
        (granularity IN ('PIPELINE', 'TAG') AND target_value IS NOT NULL)
    )
);

CREATE INDEX idx_subscriptions_user_id           ON subscriptions(user_id);
CREATE INDEX idx_subscriptions_gran_target       ON subscriptions(granularity, target_value);
-- L3-SUB-001: uniqueness of the (user, granularity, target) triple. Cannot
-- rely on a unique index over a nullable target_value in SQLite (multiple
-- NULLs are permitted); enforce via two partial indexes instead.
CREATE UNIQUE INDEX idx_subscriptions_unique_nonglobal
    ON subscriptions(user_id, granularity, target_value)
    WHERE target_value IS NOT NULL;
CREATE UNIQUE INDEX idx_subscriptions_unique_global
    ON subscriptions(user_id)
    WHERE granularity = 'GLOBAL';

-- ---------------------------------------------------------------------
-- audit_log
-- ---------------------------------------------------------------------
-- Append-only ledger. ``details_json`` carries the structured event
-- payload (run_id, prior_state, new_state, failure_reason, etc.).
CREATE TABLE audit_log (
    audit_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    action        TEXT NOT NULL,
    actor         TEXT NOT NULL,
    resource      TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    details_json  TEXT NOT NULL,
    CHECK (outcome IN ('SUCCESS', 'FAILURE'))
);

CREATE INDEX idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_log_resource  ON audit_log(resource);
CREATE INDEX idx_audit_log_action    ON audit_log(action);
