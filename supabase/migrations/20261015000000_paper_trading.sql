-- Path A paper trading mirror. state.json in the shared project folder stays the
-- source of truth; these tables are an idempotent copy for queries and dashboards.
-- Private schema, RLS on, no policies: only the service role / SQL editor can read or write.

create schema if not exists paper;
revoke all on schema paper from anon, authenticated;

create table if not exists paper.books (
  rules_version text primary key,
  frozen_branch text not null,
  start_equity  numeric not null,
  opened_on     date not null,
  updated_at    timestamptz not null default now()
);

create table if not exists paper.journal (
  rules_version text not null references paper.books on delete cascade,
  seq           integer not null,               -- position in state.json journal
  close_date    date not null,
  kind          text not null check (kind in ('open', 'decision', 'trade', 'no_decision')),
  weight        numeric not null,
  at            timestamptz,
  notes         jsonb not null default '[]',
  primary key (rules_version, seq)
);

create table if not exists paper.trades (
  rules_version text not null references paper.books on delete cascade,
  book          text not null check (book in ('paper', 'buy_and_hold')),
  filled_at     timestamptz not null,
  side          text not null check (side in ('BUY', 'SELL')),
  price         numeric not null,
  qty           numeric not null,
  value         numeric not null,
  primary key (rules_version, book, filled_at)
);

create table if not exists paper.daily_equity (
  rules_version        text not null references paper.books on delete cascade,
  day                  date not null,
  close                numeric not null,
  weight_held_at_close numeric not null,
  equity               numeric not null,
  buy_and_hold         numeric not null,
  primary key (rules_version, day)
);

create table if not exists paper.risk_checks (
  day        date primary key,
  run_at     timestamptz not null default now(),
  summary    jsonb not null
);

alter table paper.books        enable row level security;
alter table paper.journal      enable row level security;
alter table paper.trades       enable row level security;
alter table paper.daily_equity enable row level security;
alter table paper.risk_checks  enable row level security;
