-- Configured bots (docs/m4-bot-config-plan.md §3.2, platform plan C1).
-- trading.bots: one append-only row per revision of a bot's config (body bot-config-v1) with its sha256.
-- trading.bot_approvals: append-only lifecycle events (submit, approve, reject, retire), each naming the
-- revision and hash it is about, who did it and when. running/halted are not stored here: they come
-- from the worker's heartbeat. The worker uses only a revision with an approve event whose hash it
-- recomputes itself. Private like the rest of `trading`: RLS on, service_role only.
create table trading.bots (
  bot_id     uuid not null,
  revision   integer not null check (revision >= 1),
  account    text not null check (account ~ '^[A-Za-z0-9_-]{1,32}$'),
  body       jsonb not null check (body ->> 'schema_version' = 'bot-config-v1'
                                   and body ->> 'account' = account
                                   and body ->> 'mode' in ('SHADOW', 'TESTNET')
                                   and body ->> 'venue' in ('SPOT_TESTNET', 'USDM_TESTNET')),
  hash       text not null check (hash ~ '^[0-9a-f]{64}$'),
  saved_by   uuid not null,
  saved_at   timestamptz not null default now(),
  request_id uuid not null unique,
  primary key (bot_id, revision),
  unique (bot_id, revision, hash)
);
create index on trading.bots (account);

create table trading.bot_approvals (
  id       bigint generated always as identity primary key,
  bot_id   uuid not null,
  revision integer not null,
  hash     text not null,
  action   text not null check (action in ('submit', 'approve', 'reject', 'retire')),
  actor    uuid not null,
  at       timestamptz not null default now(),
  note     text not null default '' check (length(note) <= 500),
  foreign key (bot_id, revision, hash) references trading.bots (bot_id, revision, hash) on delete restrict
);
create index on trading.bot_approvals (bot_id, revision);
-- One submit, and one verdict (approve or reject), per revision; one retire per bot.
create unique index bot_approvals_one_submit on trading.bot_approvals (bot_id, revision) where action = 'submit';
create unique index bot_approvals_one_verdict on trading.bot_approvals (bot_id, revision) where action in ('approve', 'reject');
create unique index bot_approvals_one_retire on trading.bot_approvals (bot_id) where action = 'retire';

create trigger bots_append_only before update or delete on trading.bots
  for each row execute function trading.forbid_change();
create trigger bot_approvals_append_only before update or delete on trading.bot_approvals
  for each row execute function trading.forbid_change();

-- A revision keeps the bot's account, follows the previous one, and a retired bot takes no more.
create function trading.bots_check_revision() returns trigger
language plpgsql set search_path = '' as $$
declare
  prev record;
begin
  select account, revision into prev from trading.bots where bot_id = new.bot_id order by revision desc limit 1;
  if prev is null then
    if new.revision <> 1 then
      raise exception 'bot % starts at revision 1, not %', new.bot_id, new.revision;
    end if;
    return new;
  end if;
  if new.revision <> prev.revision + 1 then
    raise exception 'bot % next revision is %, not %', new.bot_id, prev.revision + 1, new.revision;
  end if;
  if new.account <> prev.account then
    raise exception 'bot % runs on account %; a revision cannot move it to %', new.bot_id, prev.account, new.account;
  end if;
  if exists (select 1 from trading.bot_approvals where bot_id = new.bot_id and action = 'retire') then
    raise exception 'bot % is retired', new.bot_id;
  end if;
  return new;
end $$;
create trigger bots_check_revision before insert on trading.bots
  for each row execute function trading.bots_check_revision();

-- Every event comes from an active operator or admin; approve/reject follow a submit of the same
-- revision; approve is refused while another bot that is not retired holds an approval on the account.
create function trading.bot_approvals_check() returns trigger
language plpgsql set search_path = '' as $$
declare
  acct text;
begin
  if not exists (select 1 from trading.operator_roles
                 where user_id = new.actor and active and role in ('operator', 'admin')) then
    raise exception 'bot event % needs an active operator or admin, not %', new.action, new.actor;
  end if;
  if exists (select 1 from trading.bot_approvals where bot_id = new.bot_id and action = 'retire') then
    raise exception 'bot % is retired', new.bot_id;
  end if;
  if new.action in ('approve', 'reject') and not exists (
      select 1 from trading.bot_approvals where bot_id = new.bot_id and revision = new.revision and action = 'submit') then
    raise exception 'bot % revision % was not submitted', new.bot_id, new.revision;
  end if;
  if new.action = 'approve' then
    select account into acct from trading.bots where bot_id = new.bot_id and revision = new.revision;
    perform pg_advisory_xact_lock(hashtext('trading.bots:' || acct));
    if exists (select 1 from trading.bots b join trading.bot_approvals a on a.bot_id = b.bot_id and a.action = 'approve'
               where b.account = acct and b.bot_id <> new.bot_id
                 and not exists (select 1 from trading.bot_approvals r where r.bot_id = b.bot_id and r.action = 'retire')) then
      raise exception 'account % already has an approved bot; one bot per account', acct;
    end if;
  end if;
  return new;
end $$;
create trigger bot_approvals_check before insert on trading.bot_approvals
  for each row execute function trading.bot_approvals_check();

grant select, insert on trading.bots, trading.bot_approvals to service_role;
grant usage on all sequences in schema trading to service_role;
alter table trading.bots enable row level security;
alter table trading.bot_approvals enable row level security;
