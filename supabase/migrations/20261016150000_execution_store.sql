-- BUILDPLAN v4.2 Phase 4 (docs/phase-4-execution-plan.md, deliverable 2): durable execution store.
-- TESTNET only: every intent row must be TESTNET, so LIVE is refused here as well as in code.
-- Intent + reservation + outbox commit in one transaction (§12.2); writes carry a fencing token (§14.3).

create table trading.exec_safety (
  account        text primary key,
  state          text not null check (state in ('RECOVERING', 'RUNNING', 'SOFT_PAUSED', 'REDUCING', 'HALTING', 'HALTED')),
  epoch          bigint not null check (epoch >= 1),
  halt_reason    text,
  reducing_until timestamptz,
  updated_at     timestamptz not null default now()
);

create table trading.exec_writer_lease (
  account    text primary key,
  owner      text not null,
  token      bigint not null check (token >= 1),
  expires_at timestamptz not null
);

create table trading.exec_intents (
  client_order_id text primary key check (client_order_id ~ '^[A-Za-z0-9._-]{1,36}$'),
  account         text not null,
  mode            text not null check (mode = 'TESTNET'),
  book            text not null,
  decision_id     text not null,
  leg             text not null,
  symbol          text not null,
  side            text not null check (side in ('BUY', 'SELL')),
  order_type      text not null check (order_type in ('MARKET', 'LIMIT')),
  qty             numeric(38, 18) not null check (qty > 0),
  limit_price     numeric(38, 18) check (limit_price > 0),
  reduce_only     boolean not null default false,
  safety_epoch    bigint not null,
  created_at      timestamptz not null,
  unique (book, decision_id, leg),
  check ((order_type = 'LIMIT') = (limit_price is not null))
);
create index on trading.exec_intents (account, created_at);

create table trading.exec_reservations (
  client_order_id text primary key references trading.exec_intents on delete restrict,
  qty             numeric(38, 18) not null check (qty >= 0),
  unit_price      numeric(38, 18) not null check (unit_price > 0)
);

create table trading.exec_outbox (
  id               bigint generated always as identity primary key,
  client_order_id  text not null references trading.exec_intents on delete restrict,
  kind             text not null check (kind in ('EXECUTE_INTENT')),
  status           text not null check (status in ('PENDING', 'CLAIMED', 'DONE')),
  claimed_by_token bigint,
  unique (client_order_id, kind)
);

create table trading.exec_order_events (
  client_order_id   text not null references trading.exec_intents on delete restrict,
  seq               integer not null check (seq >= 0),
  state             text not null check (state in ('INTENT', 'SUBMITTING', 'ACKNOWLEDGED', 'UNKNOWN', 'RECONCILING',
                                                   'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED', 'EXPIRED')),
  cum_qty           numeric(38, 18) not null check (cum_qty >= 0),
  exchange_order_id text,
  reason            text not null,
  fencing_token     bigint not null,
  at                timestamptz not null,
  primary key (client_order_id, seq)
);

create table trading.exec_fills (
  exec_id         text primary key,
  client_order_id text not null references trading.exec_intents on delete restrict,
  qty             numeric(38, 18) not null check (qty > 0),
  price           numeric(38, 18) not null check (price > 0),
  fee             numeric(38, 18) not null check (fee >= 0),
  fee_asset       text not null,
  at              timestamptz not null
);
create index on trading.exec_fills (client_order_id);

create trigger exec_intents_append_only before update or delete on trading.exec_intents
  for each row execute function trading.forbid_change();
create trigger exec_order_events_append_only before update or delete on trading.exec_order_events
  for each row execute function trading.forbid_change();
create trigger exec_fills_append_only before update or delete on trading.exec_fills
  for each row execute function trading.forbid_change();

grant select, insert, update, delete on trading.exec_safety, trading.exec_writer_lease, trading.exec_intents,
  trading.exec_reservations, trading.exec_outbox, trading.exec_order_events, trading.exec_fills to service_role;
grant usage on all sequences in schema trading to service_role;

alter table trading.exec_safety enable row level security;
alter table trading.exec_writer_lease enable row level security;
alter table trading.exec_intents enable row level security;
alter table trading.exec_reservations enable row level security;
alter table trading.exec_outbox enable row level security;
alter table trading.exec_order_events enable row level security;
alter table trading.exec_fills enable row level security;
