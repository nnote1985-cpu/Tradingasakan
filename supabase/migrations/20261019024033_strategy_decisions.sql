-- Testnet plan P5 (docs/testnet-development-plan.md §5.1, §6.2): one decision per strategy book per
-- daily close, with the inputs it used frozen in (never recomputed later). Private like the rest of
-- `trading`: RLS on, service_role only. Rows are written once and only their execution fields change.
create table trading.exec_strategy_decisions (
  account        text not null,
  book           text not null,
  rules_version  text not null,
  day            date not null,                     -- the UTC daily close the decision used
  weight         numeric(10, 6),                    -- target BTC weight; null when inputs were missing
  previous_weight numeric(10, 6),
  action         text not null check (action in ('OPEN', 'REBALANCE', 'HOLD', 'NO_DECISION')),
  notes          text not null,
  inputs         jsonb not null,
  inputs_sha256  text not null,
  data_ready_at  timestamptz not null,
  decided_at     timestamptz not null,
  -- execution: the order(s) sent for this decision, filled in by the worker that holds the lease
  attempts       int not null default 0 check (attempts >= 0),
  client_order_id text,
  executed_at    timestamptz,
  outcome        text,
  primary key (account, book, day)
);
create index on trading.exec_strategy_decisions (account, book, day desc);

grant select, insert, update on trading.exec_strategy_decisions to service_role;
alter table trading.exec_strategy_decisions enable row level security;
