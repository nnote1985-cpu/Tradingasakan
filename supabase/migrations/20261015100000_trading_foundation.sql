-- BUILDPLAN v4.2 Phase 1 Foundation: minimum ledger schema (§20) in a private schema.
-- Execution tables (intents, outbox, orders, fills, protection) arrive with Phase 3/4.

create schema if not exists trading;
revoke all on schema trading from public;
revoke all on schema trading from anon, authenticated;
grant usage on schema trading to service_role;

-- Single workspace (§20.1): one settings row, operators allowlisted by Supabase user id.
create table trading.workspace_settings (
  singleton  boolean primary key default true check (singleton),
  owner_id   uuid not null,
  name       text not null default 'asakantrading',
  created_at timestamptz not null default now()
);

create table trading.operator_roles (
  user_id    uuid not null,
  role       text not null check (role in ('viewer', 'operator', 'admin', 'live_approver')),
  active     boolean not null default true,
  granted_by uuid,
  granted_at timestamptz not null default now(),
  primary key (user_id, role)
);

create table trading.strategy_versions (
  id               uuid primary key default gen_random_uuid(),
  label            text not null unique check (label ~ '^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$'),
  code_commit      text not null check (code_commit ~ '^[0-9a-f]{7,40}$'),
  frozen_ref       text,
  config_hash      text,
  parent_id        uuid references trading.strategy_versions,
  artifact_pointer text,
  created_at       timestamptz not null default now()
);

-- Active version kept apart from evidence status (§18.1).
create table trading.strategy_registry (
  singleton              boolean primary key default true check (singleton),
  working_baseline_id    uuid references trading.strategy_versions,
  validated_champion_id  uuid references trading.strategy_versions,
  live_active_version_id uuid references trading.strategy_versions,
  evidence_scope         text,
  updated_at             timestamptz not null default now()
);

create table trading.portfolios (
  id                uuid primary key default gen_random_uuid(),
  label             text not null unique,
  owner_id          uuid not null,
  mode              text not null check (mode in ('BACKTEST', 'HISTORICAL_REPLAY', 'SHADOW', 'TESTNET', 'LIVE')),
  account_id        uuid,                                  -- null in SHADOW
  currency          text not null,
  initial_equity    numeric(38, 18) not null check (initial_equity > 0),
  active_version_id uuid references trading.strategy_versions,
  created_at        timestamptz not null default now(),
  check (mode in ('SHADOW', 'BACKTEST', 'HISTORICAL_REPLAY') or account_id is not null)
);
create index on trading.portfolios (active_version_id);

create table trading.equity_snapshots (
  portfolio_id     uuid not null references trading.portfolios on delete restrict,
  at               timestamptz not null,
  trading_equity   numeric(38, 18) not null,
  economic_equity  numeric(38, 18) not null,
  mark_price       numeric(38, 18) check (mark_price > 0),
  exposure         numeric(38, 18),
  high_water_mark  numeric(38, 18),
  quality          text not null default 'ok',
  primary key (portfolio_id, at)
);

-- Double-entry journal (§11.3): one currency per entry, lines must sum to zero.
create table trading.journal_entries (
  id               uuid primary key default gen_random_uuid(),
  portfolio_id     uuid not null references trading.portfolios on delete restrict,
  source_event_key text not null unique,
  currency         text not null,
  occurred_at      timestamptz not null,
  reversal_of      uuid references trading.journal_entries,
  memo             text,
  created_at       timestamptz not null default now()
);
create index on trading.journal_entries (portfolio_id, occurred_at);

create table trading.journal_lines (
  entry_id       uuid not null references trading.journal_entries on delete restrict,
  line_no        integer not null,
  ledger_account text not null,
  amount         numeric(38, 18) not null check (amount <> 0),
  primary key (entry_id, line_no)
);

create function trading.check_journal_balanced() returns trigger
language plpgsql set search_path = '' as $$
declare
  eid uuid := coalesce(new.entry_id, old.entry_id);
  total numeric;
begin
  select coalesce(sum(amount), 0) into total from trading.journal_lines where entry_id = eid;
  if total <> 0 then
    raise exception 'journal entry % is not balanced (sum %)', eid, total;
  end if;
  return null;
end $$;

create constraint trigger journal_balanced
  after insert or update or delete on trading.journal_lines
  deferrable initially deferred
  for each row execute function trading.check_journal_balanced();

-- Immutable audit trail (§20.1 incidents / audit_events).
create table trading.audit_events (
  id          bigint generated always as identity primary key,
  at          timestamptz not null default now(),
  actor       uuid,
  action      text not null,
  target      text,
  reason      text,
  before_hash text,
  after_hash  text
);

create function trading.forbid_change() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception '% on %.% is not allowed (append-only)', tg_op, tg_table_schema, tg_table_name;
end $$;

create trigger audit_events_append_only before update or delete on trading.audit_events
  for each row execute function trading.forbid_change();
create trigger journal_entries_append_only before update or delete on trading.journal_entries
  for each row execute function trading.forbid_change();
create trigger journal_lines_append_only before update or delete on trading.journal_lines
  for each row execute function trading.forbid_change();

-- Server-side access only: service_role through the backend, nothing for browser roles.
grant select, insert, update, delete on all tables in schema trading to service_role;
grant usage on all sequences in schema trading to service_role;
alter default privileges in schema trading revoke all on tables from anon, authenticated;

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'trading' loop
    execute format('alter table trading.%I enable row level security', t);
  end loop;
end $$;
