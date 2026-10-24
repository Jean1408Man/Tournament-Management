CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tournaments (
    tournament_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('created', 'running', 'completed', 'blocked', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    tournament_id TEXT NOT NULL REFERENCES tournaments(tournament_id),
    state TEXT NOT NULL CHECK (state IN ('waiting', 'ready', 'running', 'completed', 'blocked', 'cancelled')),
    spec JSONB NOT NULL,
    current_attempt_id TEXT,
    accepted_attempt_id TEXT UNIQUE,
    winner_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id),
    generation INTEGER NOT NULL CHECK (generation > 0),
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'accepted', 'failed', 'lost', 'expired', 'discarded', 'cancelled')),
    spec JSONB NOT NULL,
    outcome JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (match_id, generation)
);

ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_current_attempt_id_fkey;
ALTER TABLE matches ADD CONSTRAINT matches_current_attempt_id_fkey
    FOREIGN KEY (current_attempt_id) REFERENCES attempts(attempt_id) DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX IF NOT EXISTS matches_tournament_state ON matches (tournament_id, state);
CREATE INDEX IF NOT EXISTS attempts_match_state ON attempts (match_id, state);

CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    tournament_id TEXT NOT NULL REFERENCES tournaments(tournament_id),
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tournament_id, sequence)
);

INSERT INTO schema_migrations (version) VALUES ('0001_initial') ON CONFLICT DO NOTHING;
