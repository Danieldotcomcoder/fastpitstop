-- f1_store schema, migration 003: Phase 4.3 -- the semantic index over
-- radio_transcripts. Adds a local sentence embedding plus the race context
-- (lap number, classification position, track status) captured at index
-- time, ON/BESIDE the existing table -- CLAUDE.md's Phase 4 invariant is
-- "adding transcripts + embeddings as a SEPARATE store keyed to it" (i.e.
-- separate from team_radio), which this satisfies by extending that same
-- separate store rather than inventing a third one. Applied by the
-- existing f1-store-migrate mechanism, same as migration 002.
--
-- pgvector: requires an instance with the `vector` extension available
-- (see python/f1_store/docker-compose.yml's comment -- the plain
-- timescaledb image does not bundle it; the -ha image does). Embedding
-- dimension (384) matches the shipped default model,
-- sentence-transformers/all-MiniLM-L6-v2 (f1_radio.embeddings.EMBEDDING_DIM)
-- -- switching to a model with a different output dimension needs a new
-- migration to change the column type.
--
-- No ANN index (HNSW/IVFFlat) here: a season's radio corpus is at most a
-- few thousand rows, where pgvector's brute-force `<=>` operator scan is
-- already fast -- an index here would be premature optimization for a
-- corpus size that does not need it, and its syntax varies by pgvector
-- version. Add one if a real corpus ever outgrows a sequential scan.
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE radio_transcripts
    ADD COLUMN IF NOT EXISTS embedding vector(384),
    ADD COLUMN IF NOT EXISTS embedding_model_version text,
    ADD COLUMN IF NOT EXISTS lap_number integer,
    ADD COLUMN IF NOT EXISTS position integer,
    ADD COLUMN IF NOT EXISTS track_status text,
    ADD COLUMN IF NOT EXISTS track_status_flags text[];
