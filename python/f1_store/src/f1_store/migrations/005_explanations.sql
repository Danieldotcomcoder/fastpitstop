-- Why-engine explanations, cached per anomaly so each moment is narrated
-- once, ever. CLAUDE.md's Phase 5 budget invariant: "cache explanations per
-- anomaly; never re-generate on request".
--
-- Persistent rather than in-process because the narrator runs on a free
-- tier capped at 50 requests a day. An in-memory cache was emptied by every
-- deploy and restart, so the same race's story was paid for again each time
-- and a day's quota could be gone before a single new moment was asked for.
--
-- Keyed by narration_version as well as anomaly_id: changing the prompt or
-- the evidence rules must produce new sentences, not serve ones written
-- under the old rules. `f1_why.NARRATION_VERSION` names the current one.
-- Outages ("the narration service did not respond") are never written here;
-- see f1_why.narration.explain_cached.
CREATE TABLE IF NOT EXISTS explanations (
    anomaly_id          TEXT        NOT NULL,
    narration_version   TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    explanation         JSONB       NOT NULL,
    CONSTRAINT explanations_pk PRIMARY KEY (anomaly_id, narration_version)
);
