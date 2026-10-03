-- Runs once, when the postgres-data volume is first created. The identity store (P4) has its
-- own database; its schema and the tarn_auth role come from `tarn identity upgrade`
-- (make migrate), which also creates this database on volumes made before P4.
CREATE DATABASE tarn_identity;
