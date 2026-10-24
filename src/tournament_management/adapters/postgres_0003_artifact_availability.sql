-- Los metadatos de 3.1 no prueban que existan bytes. Solo el servicio de registro
-- activa disponibilidad tras publicar y sincronizar el objeto completo.
ALTER TABLE artifacts ADD COLUMN available BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE artifacts ADD COLUMN published_at TIMESTAMPTZ;
ALTER TABLE artifacts ADD CONSTRAINT artifact_publication_consistent
    CHECK (available = (published_at IS NOT NULL));
