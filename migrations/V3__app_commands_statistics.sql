-- Revises: V2
-- Creation Date: 2023-03-28 08:22:34.547143 UTC
-- Reason: app_commands_statistics

ALTER TABLE commands ADD COLUMN IF NOT EXISTS app_command BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS commands_app_command_idx ON commands (app_command);