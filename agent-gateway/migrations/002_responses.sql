CREATE TABLE IF NOT EXISTS responses (
    user_id     text NOT NULL,
    id          text NOT NULL,
    payload     bytea NOT NULL,           -- encrypted JSON: the visible conversation after this response
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS responses_created ON responses (created_at);
