-- f1_store schema, migration 002: the Phase 4 transcript store.
--
-- A SEPARATE table from team_radio (CLAUDE.md's Phase 4 invariant: "adding
-- transcripts + embeddings as a SEPARATE store keyed to it"), applied by the
-- same f1-store-migrate mechanism as migration 001 so the whole project
-- keeps one DSN, one schema_migrations history. Owned and queried by
-- f1_radio (python/f1_radio/src/f1_radio/store.py), not by f1_store's own
-- DomainEvent-derived sink/rows machinery -- a transcript is derived data
-- written by an offline transcription step, not a domain event.

-- ---------------------------------------------------------------------------
-- radio_transcripts: at most one transcript per clip. `driver`/`captured_at`
-- come straight from the originating TeamRadioClip event; `audio_path` is
-- f1_radio's own deterministic cache path (see f1_radio.cache), included in
-- the identity because a clip with no cached audio has nothing to
-- transcribe. `segments` is the model's own per-segment timing/text detail
-- (jsonb, shape owned by whichever transcription model writes it).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS radio_transcripts (
    session_year    integer     NOT NULL,
    session_round   integer     NOT NULL,
    session_type    text        NOT NULL,
    driver          text,
    captured_at     timestamptz,
    audio_path      text        NOT NULL,
    transcript_text text,
    language        text,
    confidence      double precision,
    model_version   text        NOT NULL,
    segments        jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT radio_transcripts_clip_unique
        UNIQUE (session_year, session_round, session_type, driver, captured_at, audio_path)
);
CREATE INDEX IF NOT EXISTS radio_transcripts_session_idx
    ON radio_transcripts (session_year, session_round, session_type);
