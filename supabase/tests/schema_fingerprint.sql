-- One-row fingerprint (row count + md5) of the paper and trading schemas: columns, constraints,
-- indexes, triggers, function bodies, RLS flags and grants to anon/authenticated/service_role.
-- Run it on Supabase and compare with supabase/schema_fingerprint.txt; the migration test runs it on a
-- database rebuilt from supabase/migrations.
select count(*) n, md5(string_agg(kind||'|'||name||'|'||coalesce(detail,''), E'\n' order by kind collate "C", name collate "C")) h from (select kind, name, detail from (
  select 'column' kind, table_schema||'.'||table_name||'.'||column_name name,
         data_type||coalesce('('||numeric_precision||','||numeric_scale||')','')||' null='||is_nullable||' default='||coalesce(column_default,'') detail
    from information_schema.columns where table_schema in ('paper','trading')
  union all
  select 'constraint', n.nspname||'.'||c.conrelid::regclass::text||'.'||c.conname, pg_get_constraintdef(c.oid)
    from pg_constraint c join pg_namespace n on n.oid = c.connamespace where n.nspname in ('paper','trading')
  union all
  select 'index', schemaname||'.'||indexname, indexdef from pg_indexes where schemaname in ('paper','trading')
  union all
  select 'trigger', n.nspname||'.'||t.tgrelid::regclass::text||'.'||t.tgname, pg_get_triggerdef(t.oid)
    from pg_trigger t join pg_class r on r.oid = t.tgrelid join pg_namespace n on n.oid = r.relnamespace
    where not t.tgisinternal and n.nspname in ('paper','trading')
  union all
  select 'function', n.nspname||'.'||p.proname, md5(pg_get_functiondef(p.oid))
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ('paper','trading')
  union all
  select 'rls', n.nspname||'.'||c.relname, c.relrowsecurity::text
    from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('paper','trading') and c.relkind = 'r'
  union all
  select 'grant', table_schema||'.'||table_name||'.'||grantee, string_agg(privilege_type, ',' order by privilege_type)
    from information_schema.role_table_grants where table_schema in ('paper','trading')
      and grantee in ('anon','authenticated','service_role') group by table_schema, table_name, grantee
) x order by kind, name) f;