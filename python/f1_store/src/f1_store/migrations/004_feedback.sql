-- Beta feedback submissions. Postgres is the source of truth; email is a
-- notification only. Applied by the existing f1-store-migrate mechanism.
CREATE TABLE IF NOT EXISTS feedback_submissions (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_hash         TEXT NOT NULL,
    route           TEXT NOT NULL,
    what_doing      TEXT NOT NULL,
    what_happened   TEXT NOT NULL,
    what_expected   TEXT NOT NULL,
    reply_email     TEXT,
    diagnostics     JSONB NOT NULL DEFAULT '{}'::jsonb,
    email_sent      BOOLEAN NOT NULL DEFAULT FALSE,
    email_error     TEXT
);

CREATE INDEX IF NOT EXISTS feedback_submissions_created_at_idx
    ON feedback_submissions (created_at DESC);

CREATE INDEX IF NOT EXISTS feedback_submissions_ip_hash_created_at_idx
    ON feedback_submissions (ip_hash, created_at DESC);
