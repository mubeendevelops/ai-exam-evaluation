-- Runs once, when the postgres-data volume is first created.
-- Roles, row-level security and the schema arrive with the migrations in P3.
CREATE EXTENSION IF NOT EXISTS vector;
