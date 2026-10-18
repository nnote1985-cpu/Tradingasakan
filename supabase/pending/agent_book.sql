-- AI agent book, step 1 (docs/agent-book-plan.md §3, §6). NOT APPLIED: waits for Note's OK, then moves to
-- migrations/<recorded version>_agent_book.sql with history + fingerprint in the same PR (supabase/README.md).
-- Signals the Decider asked for, every model call, and every tool call with its full response, so each decision
-- can be replayed. Private like the rest of `trading`: RLS on, service_role only.

-- A signal is created ACTIVE and ends once (FIRED, EXPIRED or CANCELLED); its condition and lifetime never change.
create table trading.agent_signals (
  id             uuid primary key default gen_random_uuid(),
  book           text not null,
  condition      text not null check (condition in ('price_close_above', 'price_close_below', 'price_touch',
                   'rsi_below', 'rsi_above', 'move_pct', 'volume_spike', 'funding_above', 'funding_below', 'time_check')),
  params         jsonb not null check (jsonb_typeof(params) = 'object'),
  created_by_run uuid,
  created_at     timestamptz not null default now(),
  expires_at     timestamptz not null,
  status         text not null default 'ACTIVE' check (status in ('ACTIVE', 'FIRED', 'EXPIRED', 'CANCELLED')),
  ended_at       timestamptz,
  fired_bar_open timestamptz,
  check (expires_at > created_at and expires_at <= created_at + interval '72 hours'),
  check ((status = 'ACTIVE') = (ended_at is null)),
  check ((status = 'FIRED') = (fired_bar_open is not null))
);
create index agent_signals_active on trading.agent_signals (book) where status = 'ACTIVE';

create function trading.agent_signal_end_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' or old.status <> 'ACTIVE' or new.status = 'ACTIVE'
     or (new.id, new.book, new.condition, new.params, new.created_by_run, new.created_at, new.expires_at)
        is distinct from (old.id, old.book, old.condition, old.params, old.created_by_run, old.created_at, old.expires_at) then
    raise exception '% on trading.agent_signals is only allowed to end an active signal', tg_op;
  end if;
  return new;
end $$;
create trigger agent_signals_end_only before update or delete on trading.agent_signals
  for each row execute function trading.agent_signal_end_only();

-- At most 5 active signals per book (signals.MAX_ACTIVE); checked under a per-book advisory lock.
create function trading.agent_signal_cap() returns trigger
language plpgsql set search_path = '' as $$
begin
  perform pg_advisory_xact_lock(hashtext('agent_signals:' || new.book));
  if (select count(*) from trading.agent_signals where book = new.book and status = 'ACTIVE') >= 5 then
    raise exception 'book % already has 5 active signals', new.book;
  end if;
  return new;
end $$;
create trigger agent_signals_cap before insert on trading.agent_signals
  for each row execute function trading.agent_signal_cap();

-- One row per model call: the three analysts, the Decider and the Veto (design §4–5).
create table trading.agent_runs (
  id             uuid primary key default gen_random_uuid(),
  book           text not null,
  decision_id    uuid not null,                 -- groups the five calls made for one trigger
  signal_id      uuid references trading.agent_signals on delete restrict,
  role           text not null check (role in ('analyst_news', 'analyst_price', 'analyst_market', 'decider', 'veto')),
  model          text not null,
  prompt_version text not null,
  started_at     timestamptz not null,
  finished_at    timestamptz,
  input_tokens   integer check (input_tokens >= 0),
  output_tokens  integer check (output_tokens >= 0),
  cost_usd       numeric(12, 6) check (cost_usd >= 0),
  outcome        text not null check (outcome in ('OK', 'TIMEOUT', 'ERROR', 'SCHEMA_INVALID', 'BUDGET_BLOCKED')),
  output         jsonb
);
create index agent_runs_decision on trading.agent_runs (decision_id);
create index agent_runs_cost on trading.agent_runs (book, started_at);

-- Every tool call a run made, with the full response, so a decision is explained by what the model actually saw.
create table trading.agent_tool_calls (
  run_id          uuid not null references trading.agent_runs on delete restrict,
  seq             integer not null check (seq >= 1),
  tool            text not null,
  request         jsonb not null,
  response        jsonb,
  response_sha256 text check (response_sha256 ~ '^[0-9a-f]{64}$'),
  error           text,
  called_at       timestamptz not null,
  primary key (run_id, seq),
  check ((response is null) <> (error is null))
);

create trigger agent_runs_append_only before update or delete on trading.agent_runs
  for each row when (old.finished_at is not null) execute function trading.forbid_change();
create trigger agent_tool_calls_append_only before update or delete on trading.agent_tool_calls
  for each row execute function trading.forbid_change();

grant select, insert, update on trading.agent_signals, trading.agent_runs to service_role;
grant select, insert on trading.agent_tool_calls to service_role;

alter table trading.agent_signals enable row level security;
alter table trading.agent_runs enable row level security;
alter table trading.agent_tool_calls enable row level security;
