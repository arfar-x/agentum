ALTER TABLE threads ADD COLUMN IF NOT EXISTS client_opt_in boolean NOT NULL DEFAULT false;
