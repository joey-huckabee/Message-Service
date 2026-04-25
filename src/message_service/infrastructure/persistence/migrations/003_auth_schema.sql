-- Local-account authentication schema (Increment 16).
--
-- Adds password storage to the existing `users` table and introduces
-- the `sessions` table for server-side session records.
--
-- Per L1-AUTH-001 / L2-AUTH-001: passwords are stored only as Argon2id
-- hashes; the column is named `password_hash` and never holds plaintext.
-- Per L2-AUTH-004: session tokens are at least 128 bits of entropy;
-- ONLY the SHA-256 digest of the token is persisted (per L3-AUTH-007),
-- the plaintext token never enters the database.
-- Per L1-AUTH-002 / L2-AUTH-006: `last_activity_at` updates on every
-- authenticated request; the idle-timeout check is enforced by the
-- per-request middleware in Increment 17.

-- ---------------------------------------------------------------------
-- users.password_hash (column add)
-- ---------------------------------------------------------------------
-- ALTER TABLE … ADD COLUMN can't add a NOT NULL column without a
-- default; default empty-string is fine because no rows exist yet
-- (the user-management workflow doesn't ship until Increment 19).
-- A future increment that adds users via the dashboard SHALL replace
-- the empty default with a real Argon2id hash; rows whose
-- password_hash is empty cannot authenticate (the verifier rejects
-- empty input).
ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT '';

-- Admins are flagged via a column rather than a separate role table
-- because v1 has only this single distinction. Future role expansion
-- would migrate to a roles join table; the column is opt-in for that
-- upgrade.
ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0
    CHECK (is_admin IN (0, 1));

-- ---------------------------------------------------------------------
-- sessions
-- ---------------------------------------------------------------------
CREATE TABLE sessions (
    -- L3-AUTH-007: store the SHA-256 hash, never the plaintext token.
    -- Hex-encoded for SQL queryability; 64 chars for SHA-256.
    token_hash         TEXT PRIMARY KEY,
    user_id            INTEGER NOT NULL,
    created_at         TEXT NOT NULL,
    last_activity_at   TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    -- Defense in depth: token hash is hex; reject anything malformed.
    CHECK (length(token_hash) = 64),
    -- Activity timestamp is monotonically non-decreasing relative to
    -- creation (clock-skew tolerated; backward jumps caught here).
    CHECK (last_activity_at >= created_at)
);

-- Common queries: lookup by token (PRIMARY KEY), enumerate per user
-- (for "list my sessions" / mass logout), expiry sweeps by activity.
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_last_activity_at ON sessions(last_activity_at);
