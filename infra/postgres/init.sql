-- Extensions required by the product (PRD / implementation plan).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Non-superuser role used by the app (RLS enforced).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'repo_app') THEN
    CREATE ROLE repo_app LOGIN PASSWORD 'repo_app';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE repo_understanding TO repo_app;
GRANT USAGE ON SCHEMA public TO repo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO repo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO repo_app;