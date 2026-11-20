-- Futures desk stage 2 (docs/futures-desk-plan.md §7): manual order requests from the web page.
-- The API checks a ticket and queues it here; the futures worker (with FUTURES_ORDERS_ENABLED=true)
-- checks it again on fresh data, sends it to the Binance Futures Demo and writes the outcome to the
-- same row. Each row is sent at most once: the worker marks it SENT before the first exchange call.
-- Private like the rest of `trading`: RLS on, service_role only.
create table trading.fut_order_requests (
  id           bigint generated always as identity primary key,
  account      text not null check (account ~ '^[A-Za-z0-9_-]{1,32}$'),
  kind         text not null check (kind in ('OPEN', 'CLOSE', 'MOVE_STOP')),
  symbol       text not null,
  side         text check (side in ('LONG', 'SHORT')),
  stop_price   numeric(38, 18) check (stop_price > 0),
  take_profit  numeric(38, 18) check (take_profit > 0),
  leverage     integer check (leverage between 1 and 125),
  qty          numeric(38, 18) check (qty > 0),        -- the previewed size; the worker never sends more
  ref_price    numeric(38, 18),
  risk_usdt    numeric(38, 18),
  actor        text not null check (length(btrim(actor)) > 0),
  request_id   uuid not null unique,
  requested_at timestamptz not null default now(),
  status       text not null default 'PENDING'
               check (status in ('PENDING', 'SENT', 'DONE', 'REFUSED', 'FAILED', 'EXPIRED')),
  handled_at   timestamptz,
  outcome      text,
  detail       jsonb not null default '{}'::jsonb,
  check (kind <> 'OPEN' or (side is not null and stop_price is not null and leverage is not null and qty is not null)),
  check (kind <> 'MOVE_STOP' or stop_price is not null),
  check ((status in ('PENDING', 'SENT')) = (handled_at is null))
);
create index fut_order_requests_open on trading.fut_order_requests (account, id) where status in ('PENDING', 'SENT');
create index on trading.fut_order_requests (account, requested_at desc);

-- Identity and the ticket never change; only the status moves forward (PENDING -> SENT -> final, or PENDING -> final).
create function trading.fut_order_requests_forward_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE'
     or (new.id, new.account, new.kind, new.symbol, new.side, new.stop_price, new.take_profit, new.leverage, new.qty,
         new.ref_price, new.risk_usdt, new.actor, new.request_id, new.requested_at)
        is distinct from (old.id, old.account, old.kind, old.symbol, old.side, old.stop_price, old.take_profit, old.leverage,
         old.qty, old.ref_price, old.risk_usdt, old.actor, old.request_id, old.requested_at)
     or old.status not in ('PENDING', 'SENT')
     or (old.status = 'SENT' and new.status = 'PENDING') then
    raise exception '% on trading.fut_order_requests may only move a request forward', tg_op;
  end if;
  return new;
end $$;
create trigger fut_order_requests_forward_only before update or delete on trading.fut_order_requests
  for each row execute function trading.fut_order_requests_forward_only();

grant select, insert, update on trading.fut_order_requests to service_role;
grant usage on all sequences in schema trading to service_role;
alter table trading.fut_order_requests enable row level security;
