-- Add the per-stage email_body_position column (L3-AGGR-018, R-AGGR-001).
--
-- A stage that submits an email body contribution declares where its
-- content is placed relative to the run-level summary block in the
-- assembled email body (L2-AGGR-003). v0.1.0 stored the contribution
-- context (email_body_context_json) but not its position; this column
-- completes the pair.
--
-- Invariant (mirrored in Stage.__post_init__): email_body_position is
-- non-NULL iff email_body_context_json is non-NULL. A column-level
-- CHECK constrains the value domain; NULL passes (CHECK fails only on
-- FALSE), which is correct for stages with no email body contribution.
--
-- Backfill: any row written before this migration that carries an
-- email_body_context_json but (necessarily) no position is set to
-- AFTER_STAGES_SUMMARY — the same default the gRPC boundary applies to
-- an UNSPECIFIED position (L3-AGGR-004) — so the invariant holds for
-- pre-existing rows the moment they are next loaded.

ALTER TABLE stages ADD COLUMN email_body_position TEXT
    CHECK (email_body_position IN ('BEFORE_STAGES_SUMMARY', 'AFTER_STAGES_SUMMARY'));

UPDATE stages
SET email_body_position = 'AFTER_STAGES_SUMMARY'
WHERE email_body_context_json IS NOT NULL;
