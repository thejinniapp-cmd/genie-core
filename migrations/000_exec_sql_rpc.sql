-- Run this first in Supabase SQL Editor to enable python migration runner.
-- Then run: python scripts/run_migration.py 005

create or replace function exec_sql(sql text) returns void
language plpgsql
as $$
begin
  execute sql;
end;
$$;
