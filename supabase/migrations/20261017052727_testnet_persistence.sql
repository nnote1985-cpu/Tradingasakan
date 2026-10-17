-- Testnet plan P3 (docs/testnet-development-plan.md §7.2, §7.3, §8.6): durable records the worker writes.
-- Intents, reservations, outbox, order events and fills already live in *_execution_store.sql; this adds
-- account epochs, closed market bars, Testnet trades with a per-asset balanced journal, reconcile runs,
-- reconciled balances and the worker heartbeat. Private like the rest of `trading`: RLS on, service_role only.

-- One Testnet trial segment per epoch. A Testnet reset closes the epoch (INTERRUPTED_TESTNET_RESET) and
-- the new balances open the next one; old rows stay (§8.6).
create table trading.exec_account_epochs (
  account          text not null,
  epoch            integer not null check (epoch >= 1),
  venue            text not null check (venue = 'BINANCE_SPOT_TESTNET'),
  status           text not null check (status in ('OPEN', 'CLOSED', 'INTERRUPTED_TESTNET_RESET')),
  opened_at        timestamptz not null,
  opening_balances jsonb not null,
  closed_at        timestamptz,
  close_reason     text,
  primary key (account, epoch),
  check ((status = 'OPEN') = (closed_at is null))
);
create unique index exec_account_epochs_one_open on trading.exec_account_epochs (account) where status = 'OPEN';

-- An epoch is closed once and never reopened or deleted; identity and opening balances never change.
create function trading.epoch_close_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' or old.status <> 'OPEN' or new.status = 'OPEN'
     or (new.account, new.epoch, new.venue, new.opened_at, new.opening_balances)
        is distinct from (old.account, old.epoch, old.venue, old.opened_at, old.opening_balances) then
    raise exception '% on trading.exec_account_epochs is only allowed to close an open epoch', tg_op;
  end if;
  return new;
end $$;
create trigger exec_account_epochs_close_only before update or delete on trading.exec_account_epochs
  for each row execute function trading.epoch_close_only();

-- Closed bars only. The first value stays (insert ... on conflict do nothing); a different later value
-- goes to market_bar_revisions, so decisions frozen on the first value can still be explained (§7.3).
create table trading.market_bars (
  venue      text not null,
  symbol     text not null,
  interval   text not null,
  open_time  timestamptz not null,
  close_time timestamptz not null,
  open       numeric(38, 18) not null,
  high       numeric(38, 18) not null,
  low        numeric(38, 18) not null,
  close      numeric(38, 18) not null,
  volume     numeric(38, 18) not null,
  source     text not null,
  stored_at  timestamptz not null default now(),
  primary key (venue, symbol, interval, open_time),
  check (close_time > open_time)
);

create table trading.market_bar_revisions (
  id        bigint generated always as identity primary key,
  venue     text not null,
  symbol    text not null,
  interval  text not null,
  open_time timestamptz not null,
  open      numeric(38, 18) not null,
  high      numeric(38, 18) not null,
  low       numeric(38, 18) not null,
  close     numeric(38, 18) not null,
  volume    numeric(38, 18) not null,
  source    text not null,
  seen_at   timestamptz not null default now(),
  foreign key (venue, symbol, interval, open_time) references trading.market_bars on delete restrict
);

-- Every trade on the Testnet account, bot or not, keyed the way the exchange scopes trade ids (§7.3).
create table trading.exec_trades (
  account          text not null,
  epoch            integer not null,
  symbol           text not null,
  trade_id         bigint not null,
  order_id         bigint not null,
  client_order_id  text not null,
  side             text not null check (side in ('BUY', 'SELL')),
  qty              numeric(38, 18) not null check (qty > 0),
  price            numeric(38, 18) not null check (price > 0),
  commission       numeric(38, 18) not null check (commission >= 0),
  commission_asset text,
  traded_at        timestamptz not null,
  received_at      timestamptz not null,
  primary key (account, epoch, symbol, trade_id),
  foreign key (account, epoch) references trading.exec_account_epochs on delete restrict
);
create index on trading.exec_trades (account, epoch, traded_at);

-- Multi-asset double entry: each (entry, asset) sums to zero at commit. Fees stay in their own asset;
-- nothing is converted to USDT here (§7.3).
create table trading.exec_journal_lines (
  account        text not null,
  epoch          integer not null,
  entry_key      text not null,
  asset          text not null,
  ledger_account text not null check (ledger_account in ('WALLET', 'MARKET', 'FEES', 'OPENING')),
  amount         numeric(38, 18) not null check (amount <> 0),
  kind           text not null check (kind in ('OPENING', 'TRADE', 'FEE')),
  at             timestamptz not null,
  primary key (account, epoch, entry_key, asset, ledger_account),
  foreign key (account, epoch) references trading.exec_account_epochs on delete restrict
);

create function trading.check_exec_journal_balanced() returns trigger
language plpgsql set search_path = '' as $$
declare
  total numeric;
begin
  select coalesce(sum(amount), 0) into total from trading.exec_journal_lines
   where account = new.account and epoch = new.epoch and entry_key = new.entry_key and asset = new.asset;
  if total <> 0 then
    raise exception 'exec journal entry % (%) is not balanced (sum %)', new.entry_key, new.asset, total;
  end if;
  return null;
end $$;
create constraint trigger exec_journal_balanced
  after insert on trading.exec_journal_lines
  deferrable initially deferred
  for each row execute function trading.check_exec_journal_balanced();

-- Account checks against REST (§8.4). Stored when the verdict changes, on every discrepancy and
-- periodically, not every 30 s.
create table trading.exec_reconciliations (
  id            bigint generated always as identity primary key,
  account       text not null,
  epoch         integer,
  checked_at    timestamptz not null,
  verdict       text not null check (verdict in ('MATCH', 'DISCREPANCY', 'INCONCLUSIVE')),
  first_check   boolean not null,
  differences   jsonb not null default '[]',
  open_orders   integer not null check (open_orders >= 0),
  foreign key (account, epoch) references trading.exec_account_epochs on delete restrict
);
create index on trading.exec_reconciliations (account, checked_at desc);

-- Reconciled projection of the exchange's balances: replaced after each conclusive check; never edited
-- by hand and never the source for an order (§7.1).
create table trading.exec_balances (
  account text not null,
  epoch   integer not null,
  asset   text not null,
  free    numeric(38, 18) not null check (free >= 0),
  locked  numeric(38, 18) not null check (locked >= 0),
  as_of   timestamptz not null,
  primary key (account, epoch, asset),
  foreign key (account, epoch) references trading.exec_account_epochs on delete restrict
);

-- Latest worker heartbeat per account (about every 10 s), for the web Worker page. No secrets in body.
create table trading.worker_heartbeats (
  account      text primary key,
  owner        text not null,
  host         text not null,
  started_at   timestamptz not null,
  last_tick_at timestamptz not null,
  body         jsonb not null,
  updated_at   timestamptz not null default now()
);

create trigger market_bars_append_only before update or delete on trading.market_bars
  for each row execute function trading.forbid_change();
create trigger market_bar_revisions_append_only before update or delete on trading.market_bar_revisions
  for each row execute function trading.forbid_change();
create trigger exec_trades_append_only before update or delete on trading.exec_trades
  for each row execute function trading.forbid_change();
create trigger exec_journal_lines_append_only before update or delete on trading.exec_journal_lines
  for each row execute function trading.forbid_change();
create trigger exec_reconciliations_append_only before update or delete on trading.exec_reconciliations
  for each row execute function trading.forbid_change();

grant select, insert, update, delete on trading.exec_account_epochs, trading.market_bars, trading.market_bar_revisions,
  trading.exec_trades, trading.exec_journal_lines, trading.exec_reconciliations, trading.exec_balances,
  trading.worker_heartbeats to service_role;
grant usage on all sequences in schema trading to service_role;

alter table trading.exec_account_epochs enable row level security;
alter table trading.market_bars enable row level security;
alter table trading.market_bar_revisions enable row level security;
alter table trading.exec_trades enable row level security;
alter table trading.exec_journal_lines enable row level security;
alter table trading.exec_reconciliations enable row level security;
alter table trading.exec_balances enable row level security;
alter table trading.worker_heartbeats enable row level security;
