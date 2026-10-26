-- The fork's baseline22 already contains the complete automation-analysis
-- table, constraints, owned sequence and index now introduced upstream as v25.
-- Preserve its rows instead of replaying PostgreSQL identity DDL.
UPDATE public._schema_version SET version = 25;
