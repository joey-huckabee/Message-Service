-- Sweeper outbox table (L3-SWEEP-010).
--
-- The sweeper transitions a run to ORPHANED and inserts one row per
-- configured disposition action into this table inside a single
-- transaction. A separate dispatcher loop later claims pending rows
-- (claimed_at IS NULL), invokes the corresponding handler, and stamps
-- completed_at on success or bumps attempts + last_error on failure.
--
-- The two-phase split (enqueue inside the orphan transaction; dispatch
-- in a separate transaction) is what gives L2-SWEEP-006 its
-- exactly-once contract: a crash between enqueue and claim is safe
-- (the row survives, the dispatcher picks it up on restart); a crash
-- between claim and complete is safe (an unstamped row whose
-- claimed_at is older than the staleness window is reclaimable, or
-- attempts/last_error reflect the partial run).

CREATE TABLE sweeper_actions (
    action_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    action_name  TEXT NOT NULL,
    enqueued_at  TEXT NOT NULL,                  -- ISO-8601 UTC, "Z" suffix
    claimed_at   TEXT,                           -- NULL = not yet claimed
    completed_at TEXT,                           -- NULL = not yet completed
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,                           -- NULL = no error recorded
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    CHECK (action_name IN (
        'SEND_PARTIAL_FLAGGED', 'DISCARD_SILENTLY',
        'NOTIFY_SUBSCRIBERS', 'NOTIFY_ADMINS'
    )),
    -- claimed_at can only be set after enqueued_at, never before.
    CHECK (claimed_at IS NULL OR claimed_at >= enqueued_at),
    -- completed_at can only be set after claimed_at, never before, and
    -- never without a claim having happened first.
    CHECK (
        completed_at IS NULL
        OR (claimed_at IS NOT NULL AND completed_at >= claimed_at)
    ),
    CHECK (attempts >= 0)
);

-- Partial index for the dispatcher's FIFO claim query
-- (SELECT ... WHERE claimed_at IS NULL ORDER BY enqueued_at LIMIT N).
-- Partial index avoids carrying entries for completed rows, which
-- dominate the table once the system reaches steady state.
CREATE INDEX idx_sweeper_actions_pending
    ON sweeper_actions(enqueued_at)
    WHERE claimed_at IS NULL;

-- Operational lookup: "show me everything sweeper-related for this run"
-- (admin UI, debugging, audit cross-reference).
CREATE INDEX idx_sweeper_actions_run_id
    ON sweeper_actions(run_id);
