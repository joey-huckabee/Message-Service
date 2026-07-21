-- Index runs.created_at for the past-runs listing (L3-DASH-024).
--
-- The dashboard past-runs endpoint pages with
-- `ORDER BY created_at DESC, run_id DESC` (run_repository.list_past_runs).
-- Without an index on created_at, SQLite performs a full-table scan plus a
-- filesort on every page request, which degrades as the runs table grows.
-- The other hot columns (state, pipeline_type, updated_at) are already
-- indexed by 001_initial_schema; this closes the created_at gap.

CREATE INDEX idx_runs_created_at ON runs(created_at);
