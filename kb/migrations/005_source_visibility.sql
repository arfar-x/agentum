-- Per-source group visibility (specs/003-identity-access, FR-VIS-*).
--
-- Written by the scheduler from `sources.yaml`, read by the query path. Empty `groups` means the
-- source is visible to everyone, including callers that supply no group information. A source with
-- no row here is visible to nobody (fail closed).

CREATE TABLE IF NOT EXISTS source_visibility (
    source_id  TEXT PRIMARY KEY,
    groups     TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Existing sources keep behaving as they do today: unrestricted until an operator adds groups.
INSERT INTO source_visibility (source_id)
SELECT DISTINCT source_id FROM entry
ON CONFLICT (source_id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_reader') THEN
        EXECUTE 'GRANT SELECT ON source_visibility TO kb_reader';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_admin') THEN
        EXECUTE 'GRANT SELECT ON source_visibility TO kb_admin';
    END IF;
END $$;
