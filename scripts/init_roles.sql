-- Runs once when the postgres volume is first created.
--
-- Two roles, because row level security is only real if the application's own
-- role cannot bypass it (decision D8):
--
--   app_system  owns the tables, runs migrations, holds BYPASSRLS. Used by
--               the GPU scheduler and the nightly quota allocator only.
--   app         the application role. No BYPASSRLS: every query it makes is
--               filtered by the tenant policies.

CREATE ROLE app LOGIN PASSWORD 'app' NOBYPASSRLS;

GRANT USAGE ON SCHEMA public TO app;

-- Tables created later by migrations are covered by these default privileges.
ALTER DEFAULT PRIVILEGES FOR ROLE app_system IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app;
ALTER DEFAULT PRIVILEGES FOR ROLE app_system IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app;
