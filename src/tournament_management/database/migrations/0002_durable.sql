-- Migración aditiva: conserva las filas del esquema 0001.
-- Las definiciones antiguas quedan NULL; no se inventa un cuadro para ellas.
ALTER TABLE tournaments ADD COLUMN definition JSONB;
ALTER TABLE tournaments ADD COLUMN progress JSONB NOT NULL DEFAULT '{}';

CREATE TABLE artifacts (
    artifact_hash TEXT PRIMARY KEY CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    protocol TEXT NOT NULL CHECK (protocol = 'nim-jsonl'),
    protocol_version INTEGER NOT NULL CHECK (protocol_version = 1),
    runtime TEXT NOT NULL CHECK (runtime = 'python3.12'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE competitors (
    competitor_id TEXT PRIMARY KEY CHECK (length(trim(competitor_id)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    artifact_hash TEXT NOT NULL REFERENCES artifacts(artifact_hash),
    UNIQUE (competitor_id, artifact_hash)
);
CREATE TABLE participants (
    tournament_id TEXT NOT NULL REFERENCES tournaments(tournament_id),
    competitor_id TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY (tournament_id, competitor_id),
    UNIQUE (tournament_id, position),
    FOREIGN KEY (competitor_id, artifact_hash) REFERENCES competitors(competitor_id, artifact_hash)
);
-- Una fila por partida es la última defensa contra dos ganadores aceptados.
CREATE TABLE results (
    match_id TEXT PRIMARY KEY REFERENCES matches(match_id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id),
    tournament_id TEXT NOT NULL REFERENCES tournaments(tournament_id),
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    outcome JSONB NOT NULL,
    UNIQUE (tournament_id, sequence)
);
CREATE TABLE idempotency_keys (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    resource_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope, key)
);
ALTER TABLE attempts ADD CONSTRAINT attempts_match_identity UNIQUE (match_id, attempt_id);
-- El intento vigente y el aceptado deben pertenecer a la misma partida.
ALTER TABLE matches ADD CONSTRAINT matches_current_identity_fkey
    FOREIGN KEY (match_id, current_attempt_id) REFERENCES attempts(match_id, attempt_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE matches ADD CONSTRAINT matches_accepted_identity_fkey
    FOREIGN KEY (match_id, accepted_attempt_id) REFERENCES attempts(match_id, attempt_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE results ADD CONSTRAINT results_attempt_identity_fkey
    FOREIGN KEY (match_id, attempt_id) REFERENCES attempts(match_id, attempt_id);
