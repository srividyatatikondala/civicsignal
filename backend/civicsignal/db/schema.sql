-- CivicSignal SQLite schema, version 2 (v2: claims/claim_clusters use `attribute`, not `predicate`).
-- Portable SQL (TEXT/INTEGER, JSON stored as TEXT) to ease a later move to PostgreSQL.
-- Entity rows hold indexed columns for querying plus a `data` JSON column with the
-- full model, so nothing is lost and new fields don't need migrations.

CREATE TABLE IF NOT EXISTS investigations (
    id               TEXT PRIMARY KEY,
    query            TEXT NOT NULL,
    normalized_query TEXT NOT NULL,          -- for repeat/monitoring comparisons
    status           TEXT NOT NULL,
    integrity_status TEXT NOT NULL,
    as_of_date       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    completed_at     TEXT,
    report           TEXT NOT NULL           -- full Investigation JSON (raw responses excluded)
);
CREATE INDEX IF NOT EXISTS idx_investigations_query ON investigations(normalized_query, created_at);

-- Every query string actually sent to SerpApi, with the reason it was sent.
CREATE TABLE IF NOT EXISTS queries (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    text             TEXT NOT NULL,
    reason           TEXT NOT NULL,
    reason_detail    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS search_runs (
    id                TEXT PRIMARY KEY,
    investigation_id  TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    query_id          TEXT REFERENCES queries(id),
    engine            TEXT NOT NULL,
    status            TEXT NOT NULL,
    data_mode         TEXT,
    fetched_at        TEXT,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    result_count      INTEGER NOT NULL DEFAULT 0,
    serpapi_search_id TEXT,
    error             TEXT,
    data              TEXT NOT NULL,          -- SearchRun JSON
    raw_response      TEXT                    -- full raw SerpApi JSON, secrets scrubbed
);
CREATE INDEX IF NOT EXISTS idx_search_runs_inv ON search_runs(investigation_id);

CREATE TABLE IF NOT EXISTS sources (
    id                 TEXT PRIMARY KEY,
    investigation_id   TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    canonical_url      TEXT NOT NULL,
    registrable_domain TEXT NOT NULL,
    data               TEXT NOT NULL,
    UNIQUE (investigation_id, canonical_url)
);
CREATE INDEX IF NOT EXISTS idx_sources_domain ON sources(registrable_domain);

CREATE TABLE IF NOT EXISTS search_results (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    search_run_id    TEXT NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    source_id        TEXT REFERENCES sources(id),
    result_type      TEXT NOT NULL,
    position         INTEGER,
    canonical_url    TEXT,
    data             TEXT NOT NULL            -- full SearchResult JSON incl. raw item
);
CREATE INDEX IF NOT EXISTS idx_results_inv ON search_results(investigation_id);
CREATE INDEX IF NOT EXISTS idx_results_canonical ON search_results(canonical_url);

CREATE TABLE IF NOT EXISTS evidence_spans (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    result_id        TEXT NOT NULL REFERENCES search_results(id) ON DELETE CASCADE,
    data             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_clusters (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    subject          TEXT,
    attribute        TEXT,
    has_conflict     INTEGER NOT NULL DEFAULT 0,
    data             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    source_id        TEXT REFERENCES sources(id),
    result_id        TEXT NOT NULL REFERENCES search_results(id) ON DELETE CASCADE,
    claim_type       TEXT NOT NULL,
    subject          TEXT,
    attribute        TEXT,
    value            TEXT,
    role             TEXT NOT NULL,
    data             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_key ON claims(subject, attribute, value);

CREATE TABLE IF NOT EXISTS findings (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    detector         TEXT NOT NULL,
    kind             TEXT NOT NULL,
    scope            TEXT NOT NULL,
    strength         TEXT NOT NULL,
    data             TEXT NOT NULL            -- includes source/claim/evidence ids
);

CREATE TABLE IF NOT EXISTS detector_runs (
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    detector         TEXT NOT NULL,
    status           TEXT NOT NULL,
    message          TEXT NOT NULL DEFAULT '',
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    finding_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (investigation_id, detector)
);
