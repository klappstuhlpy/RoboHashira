-- Revises: V1
-- Creation Date: 2023-03-28 08:21:51.105164 UTC
-- Reason: playlist_changes

CREATE TABLE IF NOT EXISTS playlist (
    id SERIAL PRIMARY KEY,
    name TEXT,
    user_id BIGINT,
    created TIMESTAMP DEFAULT (now() AT TIME ZONE 'UTC'::TEXT)
);

CREATE TABLE IF NOT EXISTS playlist_entries (
    id SERIAL PRIMARY KEY,
    playlist_id INTEGER REFERENCES playlist (id) ON DELETE CASCADE ON UPDATE NO ACTION,
    name TEXT,
    url TEXT
);
