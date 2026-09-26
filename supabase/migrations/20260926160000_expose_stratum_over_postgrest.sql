-- Expose the stratum schema over the REST API.
--
-- PostgREST serves only the schemas it is told about, and the default is public.
-- Keeping our tables in their own schema rather than dumping them in public means
-- Supabase's objects and ours never collide, and the exposed surface is exactly what
-- we chose to expose - but it has to be named here or every request 404s.
--
-- Two different reloads: 'reload config' picks up db_schemas, 'reload schema' rebuilds
-- the cache of tables and functions. A new function is invisible without the second.
alter role authenticator set pgrst.db_schemas = 'public, graphql_public, stratum';
notify pgrst, 'reload config';
notify pgrst, 'reload schema';
