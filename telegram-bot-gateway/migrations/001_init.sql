CREATE TABLE links (
  telegram_user_id bigint PRIMARY KEY,
  username text NOT NULL,
  telegram_name text NOT NULL,
  api_key_enc text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pairing_codes (
  code text PRIMARY KEY,
  telegram_user_id bigint NOT NULL,
  username text NOT NULL,
  telegram_name text NOT NULL,
  chat_id bigint NOT NULL,
  created_at double precision NOT NULL
);
CREATE INDEX pairing_codes_user ON pairing_codes (telegram_user_id);

CREATE TABLE sessions (
  key text PRIMARY KEY,
  telegram_user_id bigint NOT NULL,
  model text,
  params jsonb NOT NULL DEFAULT '{}',
  messages jsonb NOT NULL DEFAULT '[]',
  pending jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_user ON sessions (telegram_user_id);

CREATE TABLE message_texts (
  chat_id bigint NOT NULL,
  message_id bigint NOT NULL,
  text text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, message_id)
);
