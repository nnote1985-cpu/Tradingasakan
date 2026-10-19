-- Testnet plan P5 (docs/testnet-development-plan.md §8.5): Guardian state and operator requests.
-- Private like the rest of `trading`: RLS on, service_role only.

-- One row per guarded book: the high-water mark and the equity at the first mark of the UTC day,
-- so drawdown and day loss survive a worker restart. A resume restarts the high-water mark.
create table trading.exec_book_risk (
  account     text not null,
  book        text not null,
  profile     text not null,
  allocation  numeric(38, 18) not null check (allocation > 0),
  hwm         numeric(38, 18) not null,
  hwm_at      timestamptz not null,
  day         date not null,
  day_open    numeric(38, 18) not null,
  last_equity numeric(38, 18) not null,
  marked_at   timestamptz not null,
  primary key (account, book)
);

-- Every alert, HALT, flat confirmation and resume, kept for good: a resume never clears the history.
create table trading.exec_risk_events (
  id       bigint generated always as identity primary key,
  account  text not null,
  book     text,
  kind     text not null check (kind in ('DAY_LOSS_ALERT', 'DRAWDOWN_ALERT', 'HALT', 'FLAT_CONFIRMED', 'RESUME')),
  equity   numeric(38, 18),
  hwm      numeric(38, 18),
  detail   text not null,
  epoch    bigint not null,
  at       timestamptz not null
);
create index on trading.exec_risk_events (account, at desc);
create trigger exec_risk_events_append_only before update or delete on trading.exec_risk_events
  for each row execute function trading.forbid_change();

-- HALT and RESUME asked for by a named person (CLI now, web later). The worker that holds the
-- writer lease carries each one out once and stamps it handled.
create table trading.exec_operator_requests (
  id           bigint generated always as identity primary key,
  account      text not null,
  kind         text not null check (kind in ('HALT', 'RESUME')),
  actor        text not null check (length(btrim(actor)) > 0),
  reason       text not null check (length(btrim(reason)) > 0),
  requested_at timestamptz not null default now(),
  handled_at   timestamptz,
  outcome      text,
  check ((handled_at is null) = (outcome is null))
);
create index exec_operator_requests_open on trading.exec_operator_requests (account, id) where handled_at is null;

grant select, insert, update on trading.exec_book_risk, trading.exec_operator_requests to service_role;
grant select, insert on trading.exec_risk_events to service_role;
grant usage on all sequences in schema trading to service_role;

alter table trading.exec_book_risk enable row level security;
alter table trading.exec_risk_events enable row level security;
alter table trading.exec_operator_requests enable row level security;
