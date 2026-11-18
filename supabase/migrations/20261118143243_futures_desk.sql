-- Futures desk stage 1 (docs/futures-desk-plan.md §4): what the futures worker reads from the Binance
-- USDⓈ-M Futures Demo account. Read-only toward the exchange: nothing here queues or records orders sent
-- by this app. Private like the rest of `trading`: RLS on, service_role only.

-- Latest view of the account, replaced on every sync (about every 10 s).
create table trading.fut_state (
  account           text primary key check (account ~ '^[A-Za-z0-9_-]{1,32}$'),
  venue             text not null check (venue = 'BINANCE_USDM_DEMO'),
  synced_at         timestamptz not null,
  wallet_balance    numeric(38, 18) not null,
  margin_balance    numeric(38, 18) not null,   -- equity = wallet + unrealized P&L
  available_balance numeric(38, 18) not null,
  unrealized_pnl    numeric(38, 18) not null,
  positions         jsonb not null check (jsonb_typeof(positions) = 'array'),
  open_orders       jsonb not null check (jsonb_typeof(open_orders) = 'array'),
  markets           jsonb not null check (jsonb_typeof(markets) = 'object'),
  body              jsonb not null,             -- worker health: owner, host, started_at, last error
  updated_at        timestamptz not null default now()
);

-- High-water mark and UTC day open for the 3% day and 10% total loss caps; survives a worker restart.
create table trading.fut_risk_state (
  account      text primary key references trading.fut_state on delete restrict,
  day          date not null,
  day_open     numeric(38, 18) not null,
  hwm          numeric(38, 18) not null,
  hwm_at       timestamptz not null,
  start_equity numeric(38, 18) not null,
  started_at   timestamptz not null,
  marked_at    timestamptz not null
);

-- Every fill on the account, from /fapi/v1/userTrades. Kept for good.
create table trading.fut_fills (
  account          text not null,
  symbol           text not null,
  trade_id         bigint not null,
  order_id         bigint not null,
  side             text not null check (side in ('BUY', 'SELL')),
  qty              numeric(38, 18) not null check (qty > 0),
  price            numeric(38, 18) not null check (price > 0),
  realized_pnl     numeric(38, 18) not null,
  commission       numeric(38, 18) not null,
  commission_asset text not null,
  maker            boolean not null,
  traded_at        timestamptz not null,
  received_at      timestamptz not null default now(),
  primary key (account, symbol, trade_id)
);
create index on trading.fut_fills (account, traded_at);

-- Funding, commission, realized P&L and transfers, from /fapi/v1/income. Kept for good.
create table trading.fut_income (
  account  text not null,
  tran_id  bigint not null,
  kind     text not null,
  symbol   text,
  amount   numeric(38, 18) not null,
  asset    text not null,
  info     text not null default '',
  trade_id bigint,
  at       timestamptz not null,
  primary key (account, tran_id, kind)
);
create index on trading.fut_income (account, at);

create trigger fut_fills_append_only before update or delete on trading.fut_fills
  for each row execute function trading.forbid_change();
create trigger fut_income_append_only before update or delete on trading.fut_income
  for each row execute function trading.forbid_change();

grant select, insert, update on trading.fut_state, trading.fut_risk_state to service_role;
grant select, insert on trading.fut_fills, trading.fut_income to service_role;

alter table trading.fut_state enable row level security;
alter table trading.fut_risk_state enable row level security;
alter table trading.fut_fills enable row level security;
alter table trading.fut_income enable row level security;
