\set ON_ERROR_STOP on
\ir /schema.sql
\getenv reader_password READER_PASSWORD
SELECT format('CREATE ROLE ruleshift_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ruleshift_reader')
\gexec
GRANT CONNECT ON DATABASE ruleshift TO ruleshift_reader;
GRANT USAGE ON SCHEMA public TO ruleshift_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ruleshift_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ruleshift_reader;
