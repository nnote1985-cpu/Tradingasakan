-- Risk events of the spot books (spot.v1 alerts, HALT, resume) and the Phase 5 AI filter answers,
-- copied from the frozen books' state by scripts/trading_sync_sql.py. Both are append-only.

create table trading.risk_events (
  id               bigint generated always as identity primary key,
  portfolio_id     uuid not null references trading.portfolios on delete restrict,
  source_event_key text not null unique,
  kind             text not null check (kind in ('alert', 'halt', 'resume')),
  at               timestamptz not null,
  reason           text,
  trading_equity   numeric(38, 18),
  high_water_mark  numeric(38, 18),
  created_at       timestamptz not null default now()
);
create index on trading.risk_events (portfolio_id, at);

-- One row per question to the AI (docs/phase-5-paired-plan.md). A filter row gates a weight
-- increase of the AI arm; a diagnostic row is recorded only and never acted on.
create table trading.agent_decisions (
  id             bigint generated always as identity primary key,
  portfolio_id   uuid not null references trading.portfolios on delete restrict,
  proposal_id    text not null unique,
  kind           text not null check (kind in ('filter', 'diagnostic')),
  decided_on     date not null,
  current_weight numeric(9, 6) not null,
  target_weight  numeric(9, 6) not null,
  decision       text not null check (decision in ('ALLOW', 'VETO', 'ABSTAIN')),
  reason_code    text not null,
  reason         text,
  attempt_status text not null,
  error_category text,
  model_id       text,
  policy_version text,
  input_tokens   integer check (input_tokens >= 0),
  output_tokens  integer check (output_tokens >= 0),
  cost_usd       numeric(18, 9) not null default 0 check (cost_usd >= 0),
  latency_ms     integer check (latency_ms >= 0),
  decided_at     timestamptz not null,
  created_at     timestamptz not null default now()
);
create index on trading.agent_decisions (portfolio_id, decided_on);

create trigger risk_events_append_only before update or delete on trading.risk_events
  for each row execute function trading.forbid_change();
create trigger agent_decisions_append_only before update or delete on trading.agent_decisions
  for each row execute function trading.forbid_change();

-- Current HALT state per portfolio: the latest halt or resume event decides.
create view trading.portfolio_risk_status with (security_invoker = true) as
select p.id as portfolio_id, p.label,
       coalesce(l.kind = 'halt', false) as halted,
       case when l.kind = 'halt' then l.at end as halted_since,
       case when l.kind = 'halt' then l.reason end as halt_reason
  from trading.portfolios p
  left join lateral (select r.kind, r.at, r.reason from trading.risk_events r
                      where r.portfolio_id = p.id and r.kind in ('halt', 'resume')
                      order by r.at desc, r.id desc limit 1) l on true;

revoke all on trading.risk_events, trading.agent_decisions, trading.portfolio_risk_status from anon, authenticated;
grant select, insert, update, delete on trading.risk_events, trading.agent_decisions to service_role;
grant select on trading.portfolio_risk_status to service_role;
grant usage on all sequences in schema trading to service_role;
alter table trading.risk_events enable row level security;
alter table trading.agent_decisions enable row level security;
